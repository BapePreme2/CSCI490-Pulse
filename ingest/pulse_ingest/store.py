from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, replace
from typing import Literal, Protocol

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from pulse_ingest.schemas import AlertRuleIn, MetricBatch

HostStatus = Literal["online", "offline"]

# A host is "online" if its last report is more recent than this. Fixed and
# generous rather than derived per-host from each agent's own interval
# (which would need tracking recent gaps per host): comfortably above the
# packaged agent's default 10s interval_seconds, while still flipping to
# offline within a demo-able amount of time if an agent is killed.
STALE_AFTER_SECONDS = 30

# xmax = 0 is the standard Postgres idiom for "this row was just inserted,
# not touched by the ON CONFLICT update branch" within the same command --
# that's how a host's first-ever contact (self-registration) is detected.
_UPSERT_HOST = """
INSERT INTO hosts (hostname) VALUES (%s)
ON CONFLICT (hostname) DO UPDATE SET last_seen_at = now()
RETURNING id, (xmax = 0) AS is_new
"""

# The no-op DO UPDATE makes RETURNING yield rows that already existed too.
_UPSERT_METRICS = """
INSERT INTO metrics (name, unit)
SELECT * FROM unnest(%s::text[], %s::text[])
ON CONFLICT (name, unit) DO UPDATE SET name = EXCLUDED.name
RETURNING id, name, unit
"""

_INSERT_VALUE = """
INSERT INTO metric_values (host_id, metric_id, ts, value, tags)
VALUES (%s, %s, to_timestamp(%s), %s, %s)
ON CONFLICT DO NOTHING
"""

_HOST_EXISTS = "SELECT 1 FROM hosts WHERE hostname = %s"

# The "key metrics" a fleet overview shows at a glance: each host's overall
# cpu.usage and memory.used/memory.total, each as a single DISTINCT ON per
# (host, metric) rather than one query per host. LEFT JOINed onto every
# host so one with no data yet still appears, with nulls.
#
# cpu.usage fans out per-core (tagged with "core"); the overall reading is
# the one lacking that tag specifically -- NOT a requirement of zero tags
# altogether, since an agent's other configured tags (environment, say)
# land on every metric it reports, overall CPU included. memory.used/total
# never fan out at all, so any reading for those names is "the" one
# regardless of its tags.
_LIST_HOSTS = """
WITH key_metrics AS (
    SELECT DISTINCT ON (v.host_id, m.name)
        v.host_id, m.name, v.value
    FROM metric_values v
    JOIN metrics m ON m.id = v.metric_id
    WHERE (m.name = 'cpu.usage' AND NOT (v.tags ? 'core'))
       OR m.name IN ('memory.used', 'memory.total')
    ORDER BY v.host_id, m.name, v.ts DESC
)
SELECT
    h.hostname,
    extract(epoch FROM h.first_seen_at),
    extract(epoch FROM h.last_seen_at),
    cpu.value,
    mem_used.value,
    mem_total.value
FROM hosts h
LEFT JOIN key_metrics cpu ON cpu.host_id = h.id AND cpu.name = 'cpu.usage'
LEFT JOIN key_metrics mem_used ON mem_used.host_id = h.id AND mem_used.name = 'memory.used'
LEFT JOIN key_metrics mem_total ON mem_total.host_id = h.id AND mem_total.name = 'memory.total'
ORDER BY h.last_seen_at DESC, h.hostname
"""

# One row per currently-reporting series: DISTINCT ON picks the latest ts
# within each (metric_id, tags) group, which is why the ORDER BY must lead
# with those same two columns.
_LATEST_QUERY = """
SELECT DISTINCT ON (v.metric_id, v.tags)
    m.name, m.unit, v.tags, v.value, extract(epoch FROM v.ts)
FROM metric_values v
JOIN hosts h ON h.id = v.host_id
JOIN metrics m ON m.id = v.metric_id
WHERE h.hostname = %s
ORDER BY v.metric_id, v.tags, v.ts DESC
"""

_HISTORY_QUERY = """
SELECT m.unit, v.tags, extract(epoch FROM v.ts), v.value
FROM metric_values v
JOIN hosts h ON h.id = v.host_id
JOIN metrics m ON m.id = v.metric_id
WHERE h.hostname = %s AND m.name = %s
  AND v.ts >= to_timestamp(%s) AND v.ts <= to_timestamp(%s)
ORDER BY m.unit, v.tags, v.ts
"""

# Every series each enabled rule watches whose latest sample is at or after
# `since`, with that series' samples from (duration_seconds + padding) before
# its latest sample onward: the sliding window ALERT-03's sustained-duration
# check walks back through. A rule's tag_filter matches a series when its
# non-null values are contained in the series' tags and none of its
# null-valued keys are present at all (see migrations/003_alert_rules.sql).
#
# The inner ts filter only bounds the scan: since a kept series' latest
# sample is >= since, none of its window starts earlier than that bound.
_RULE_SERIES_QUERY = """
WITH matched AS (
    SELECT
        r.id AS rule_id, r.name AS rule_name, r.metric_name, r.operator, r.threshold,
        r.duration_seconds, v.host_id, v.metric_id, v.tags, v.ts, v.value,
        max(v.ts) OVER (PARTITION BY r.id, v.host_id, v.metric_id, v.tags) AS latest_ts
    FROM alert_rules r
    JOIN metrics m ON m.name = r.metric_name
    JOIN metric_values v ON v.metric_id = m.id
    WHERE r.enabled
      AND (r.host_id IS NULL OR r.host_id = v.host_id)
      AND v.ts >= to_timestamp(%(since)s) - (r.duration_seconds + %(padding)s) * interval '1 second'
      AND v.tags @> jsonb_strip_nulls(r.tag_filter)
      AND NOT (v.tags ?| ARRAY(
          SELECT key FROM jsonb_each(r.tag_filter) WHERE value = 'null'::jsonb
      ))
)
SELECT
    x.rule_id, x.rule_name, x.metric_name, x.operator, x.threshold, x.duration_seconds,
    x.host_id, h.hostname, m.unit, x.tags, extract(epoch FROM x.ts), x.value
FROM matched x
JOIN hosts h ON h.id = x.host_id
JOIN metrics m ON m.id = x.metric_id
WHERE x.latest_ts >= to_timestamp(%(since)s)
  AND x.ts >= x.latest_ts - (x.duration_seconds + %(padding)s) * interval '1 second'
ORDER BY x.rule_id, x.host_id, x.metric_id, x.tags, x.ts
"""

_OPEN_ALERTS = """
SELECT
    a.id, a.rule_id, a.host_id, h.hostname, a.unit, a.tags,
    a.rule_name, a.metric_name, a.operator, a.threshold, a.duration_seconds,
    a.state, extract(epoch FROM a.pending_since), extract(epoch FROM a.fired_at),
    extract(epoch FROM a.resolved_at), a.resolve_reason, a.last_value,
    extract(epoch FROM a.last_evaluated_at)
FROM alerts a
JOIN hosts h ON h.id = a.host_id
WHERE a.state <> 'resolved'
ORDER BY a.id
"""

_INSERT_ALERT = """
INSERT INTO alerts (
    rule_id, host_id, unit, tags, rule_name, metric_name, operator, threshold,
    duration_seconds, state, pending_since, fired_at, resolved_at, resolve_reason,
    last_value, last_evaluated_at
) VALUES (
    %(rule_id)s, %(host_id)s, %(unit)s, %(tags)s, %(rule_name)s, %(metric_name)s,
    %(operator)s, %(threshold)s, %(duration_seconds)s, %(state)s,
    to_timestamp(%(pending_since)s), to_timestamp(%(fired_at)s), to_timestamp(%(resolved_at)s),
    %(resolve_reason)s, %(last_value)s, to_timestamp(%(last_evaluated_at)s)
)
RETURNING id
"""

# The series identity (rule, host, unit, tags) never changes on an update.
_UPDATE_ALERT = """
UPDATE alerts SET
    rule_name = %(rule_name)s, metric_name = %(metric_name)s, operator = %(operator)s,
    threshold = %(threshold)s, duration_seconds = %(duration_seconds)s, state = %(state)s,
    pending_since = to_timestamp(%(pending_since)s), fired_at = to_timestamp(%(fired_at)s),
    resolved_at = to_timestamp(%(resolved_at)s), resolve_reason = %(resolve_reason)s,
    last_value = %(last_value)s, last_evaluated_at = to_timestamp(%(last_evaluated_at)s)
WHERE id = %(id)s
"""

_DELETE_ALERT = "DELETE FROM alerts WHERE id = %s"

# A rule's host is exposed by hostname (NULL for fleet-wide), not host_id.
_RULE_SELECT = """
SELECT
    r.id, r.name, r.metric_name, r.operator, r.threshold, r.duration_seconds,
    h.hostname, r.tag_filter, r.enabled,
    extract(epoch FROM r.created_at), extract(epoch FROM r.updated_at)
FROM alert_rules r
LEFT JOIN hosts h ON h.id = r.host_id
"""

_INSERT_RULE = """
INSERT INTO alert_rules (name, metric_name, operator, threshold, duration_seconds, host_id, tag_filter, enabled)
VALUES (%(name)s, %(metric_name)s, %(operator)s, %(threshold)s, %(duration_seconds)s,
        %(host_id)s, %(tag_filter)s, %(enabled)s)
RETURNING id
"""

_UPDATE_RULE = """
UPDATE alert_rules SET
    name = %(name)s, metric_name = %(metric_name)s, operator = %(operator)s,
    threshold = %(threshold)s, duration_seconds = %(duration_seconds)s,
    host_id = %(host_id)s, tag_filter = %(tag_filter)s, enabled = %(enabled)s,
    updated_at = now()
WHERE id = %(id)s
RETURNING id
"""

_DELETE_RULE = "DELETE FROM alert_rules WHERE id = %s"

_HOST_ID = "SELECT id FROM hosts WHERE hostname = %s"


@dataclass(frozen=True)
class WriteResult:
    stored: int
    new_host: bool = False


@dataclass(frozen=True)
class LatestMetric:
    name: str
    unit: str
    tags: dict[str, str]
    value: float
    timestamp: float


@dataclass(frozen=True)
class HostSummary:
    hostname: str
    first_seen_at: float
    last_seen_at: float
    status: HostStatus
    cpu_usage: float | None = None
    memory_percent: float | None = None


@dataclass(frozen=True)
class HistoryPoint:
    timestamp: float
    value: float


@dataclass(frozen=True)
class HistorySeries:
    unit: str
    tags: dict[str, str]
    points: list[HistoryPoint] = field(default_factory=list)


@dataclass(frozen=True)
class RuleSeries:
    """The recent samples of one series an alert rule watches, oldest first."""

    rule_id: int
    rule_name: str
    metric_name: str
    operator: str
    threshold: float
    duration_seconds: int
    host_id: int
    hostname: str
    unit: str
    tags: dict[str, str]
    points: list[HistoryPoint] = field(default_factory=list)


@dataclass(frozen=True)
class AlertRule:
    """One row of `alert_rules`, with `host` as a hostname (None = fleet-wide)."""

    id: int
    name: str
    metric_name: str
    operator: str
    threshold: float
    duration_seconds: int
    host: str | None
    tag_filter: dict[str, str | None]
    enabled: bool
    created_at: float
    updated_at: float


class UnknownHostError(Exception):
    """A rule named a host that has never reported."""

    def __init__(self, host: str) -> None:
        super().__init__(f"Unknown host: {host}")
        self.host = host


AlertState = Literal["pending", "firing", "resolved"]
ResolveReason = Literal["recovered", "not_evaluated"]


@dataclass(frozen=True)
class Alert:
    """One row of `alerts`. `id` is None until it has been inserted."""

    id: int | None
    rule_id: int | None
    host_id: int
    hostname: str
    unit: str
    tags: dict[str, str]
    rule_name: str
    metric_name: str
    operator: str
    threshold: float
    duration_seconds: int
    state: AlertState
    pending_since: float
    last_value: float
    last_evaluated_at: float
    fired_at: float | None = None
    resolved_at: float | None = None
    resolve_reason: ResolveReason | None = None


@dataclass(frozen=True)
class AlertChange:
    """One alert transition to persist: `before` None inserts `after`,
    `after` None deletes `before`, and both present updates it."""

    kind: str
    before: Alert | None
    after: Alert | None


class MetricStore(Protocol):
    def write_batch(self, batch: MetricBatch) -> WriteResult:
        """Persist a batch atomically; already-stored samples are skipped."""


class QueryStore(Protocol):
    def host_exists(self, host: str) -> bool: ...

    def list_hosts(self, now: float | None = None) -> list[HostSummary]:
        """Every known host, most recently active first. `now` defaults to
        the real current time; overridable so online/offline is testable
        deterministically."""

    def get_host_summary(self, host: str, now: float | None = None) -> HostSummary | None:
        """One host's own summary, or None if unknown."""

    def get_latest(self, host: str) -> list[LatestMetric]:
        """The most recent value of every series (metric + tags) a host reports."""

    def get_history(self, host: str, name: str, start: float, end: float) -> list[HistorySeries]:
        """All samples for one metric name in [start, end], grouped into series by tags."""


class RuleStore(Protocol):
    def list_rules(self) -> list[AlertRule]: ...

    def get_rule(self, rule_id: int) -> AlertRule | None: ...

    def create_rule(self, rule: AlertRuleIn) -> AlertRule:
        """Raises UnknownHostError if `rule.host` has never reported."""

    def update_rule(self, rule_id: int, rule: AlertRuleIn) -> AlertRule | None:
        """Replace a rule entirely. None if it doesn't exist; raises
        UnknownHostError if `rule.host` has never reported."""

    def delete_rule(self, rule_id: int) -> bool:
        """False if it didn't exist. Its alerts are kept (rule_id SET NULL)."""


class Store(MetricStore, QueryStore, RuleStore, Protocol):
    """Everything the API needs from a storage backend."""


class AlertStore(Protocol):
    def rule_series(self, since: float, padding: float) -> list[RuleSeries]:
        """For every enabled rule, each series it matches whose latest sample
        is at or after `since`, with its samples from `duration_seconds +
        padding` before that latest sample onward. Series with nothing that
        recent are omitted."""

    def open_alerts(self) -> list[Alert]:
        """Every pending or firing alert."""

    def apply_alert_changes(self, changes: list[AlertChange]) -> list[AlertChange]:
        """Persist the changes in one transaction. Returns them with inserted
        alerts' new ids filled in."""


class PostgresMetricStore:
    def __init__(
        self,
        url: str,
        min_size: int = 1,
        max_size: int = 10,
        connect_timeout: float = 5.0,
    ) -> None:
        self._pool = ConnectionPool(
            url, min_size=min_size, max_size=max_size, timeout=connect_timeout, open=False
        )

    def open(self) -> None:
        self._pool.open()

    def close(self) -> None:
        self._pool.close()

    def write_batch(self, batch: MetricBatch) -> WriteResult:
        # Sorted so concurrent batches lock shared metric rows in the same
        # order and cannot deadlock each other.
        pairs = sorted({(m.name, m.unit) for m in batch.metrics})

        with self._pool.connection() as conn, conn.cursor() as cur:
            host_id, is_new_host = cur.execute(_UPSERT_HOST, (batch.host,)).fetchone()

            cur.execute(_UPSERT_METRICS, ([p[0] for p in pairs], [p[1] for p in pairs]))
            metric_ids = {(name, unit): metric_id for metric_id, name, unit in cur.fetchall()}

            # host_id already identifies the host, so the agent's redundant
            # "host" tag is not stored on every row.
            rows = [
                (
                    host_id,
                    metric_ids[(m.name, m.unit)],
                    m.timestamp,
                    m.value,
                    Jsonb({k: v for k, v in m.tags.items() if k != "host"}),
                )
                for m in batch.metrics
            ]
            cur.executemany(_INSERT_VALUE, rows)
            return WriteResult(stored=cur.rowcount, new_host=is_new_host)

    def host_exists(self, host: str) -> bool:
        with self._pool.connection() as conn:
            return conn.execute(_HOST_EXISTS, (host,)).fetchone() is not None

    def list_hosts(self, now: float | None = None) -> list[HostSummary]:
        now = time.time() if now is None else now
        with self._pool.connection() as conn:
            rows = conn.execute(_LIST_HOSTS).fetchall()
        return [
            HostSummary(
                hostname=hostname,
                first_seen_at=float(first),
                last_seen_at=float(last),
                status="online" if (now - float(last)) < STALE_AFTER_SECONDS else "offline",
                cpu_usage=cpu,
                memory_percent=(mem_used / mem_total * 100) if mem_used is not None and mem_total else None,
            )
            for hostname, first, last, cpu, mem_used, mem_total in rows
        ]

    def get_host_summary(self, host: str, now: float | None = None) -> HostSummary | None:
        # Reuses list_hosts() rather than a second copy of its query, so the
        # same key-metric tag-matching logic can't drift out of sync between
        # the two (a bug already hit once in the fleet-wide version).
        return next((h for h in self.list_hosts(now) if h.hostname == host), None)

    def get_latest(self, host: str) -> list[LatestMetric]:
        with self._pool.connection() as conn:
            rows = conn.execute(_LATEST_QUERY, (host,)).fetchall()
        return [
            LatestMetric(name=name, unit=unit, tags=tags, value=value, timestamp=float(ts))
            for name, unit, tags, value, ts in rows
        ]

    def get_history(self, host: str, name: str, start: float, end: float) -> list[HistorySeries]:
        with self._pool.connection() as conn:
            rows = conn.execute(_HISTORY_QUERY, (host, name, start, end)).fetchall()

        series_by_key: dict[str, HistorySeries] = {}
        order: list[str] = []
        for unit, tags, ts, value in rows:
            key = f"{unit}\x00{json.dumps(tags, sort_keys=True)}"
            if key not in series_by_key:
                series_by_key[key] = HistorySeries(unit=unit, tags=tags)
                order.append(key)
            series_by_key[key].points.append(HistoryPoint(timestamp=float(ts), value=value))
        return [series_by_key[key] for key in order]

    def rule_series(self, since: float, padding: float) -> list[RuleSeries]:
        with self._pool.connection() as conn:
            rows = conn.execute(_RULE_SERIES_QUERY, {"since": since, "padding": padding}).fetchall()

        # Rows arrive grouped by series (the ORDER BY), so a new series starts
        # whenever the key changes.
        result: list[RuleSeries] = []
        for rule_id, rule_name, metric_name, op, threshold, duration, host_id, hostname, unit, tags, ts, value in rows:
            current = result[-1] if result else None
            if current is None or (current.rule_id, current.hostname, current.unit, current.tags) != (
                rule_id, hostname, unit, tags
            ):
                current = RuleSeries(
                    rule_id=rule_id,
                    rule_name=rule_name,
                    metric_name=metric_name,
                    operator=op,
                    threshold=threshold,
                    duration_seconds=duration,
                    host_id=host_id,
                    hostname=hostname,
                    unit=unit,
                    tags=tags,
                )
                result.append(current)
            current.points.append(HistoryPoint(timestamp=float(ts), value=value))
        return result

    def list_rules(self) -> list[AlertRule]:
        with self._pool.connection() as conn:
            rows = conn.execute(_RULE_SELECT + "ORDER BY r.id").fetchall()
        return [_rule_from_row(row) for row in rows]

    def get_rule(self, rule_id: int) -> AlertRule | None:
        with self._pool.connection() as conn:
            row = conn.execute(_RULE_SELECT + "WHERE r.id = %s", (rule_id,)).fetchone()
        return None if row is None else _rule_from_row(row)

    def create_rule(self, rule: AlertRuleIn) -> AlertRule:
        with self._pool.connection() as conn, conn.transaction():
            (rule_id,) = conn.execute(_INSERT_RULE, self._rule_params(conn, rule)).fetchone()
            return _rule_from_row(conn.execute(_RULE_SELECT + "WHERE r.id = %s", (rule_id,)).fetchone())

    def update_rule(self, rule_id: int, rule: AlertRuleIn) -> AlertRule | None:
        with self._pool.connection() as conn, conn.transaction():
            params = {**self._rule_params(conn, rule), "id": rule_id}
            if conn.execute(_UPDATE_RULE, params).fetchone() is None:
                return None
            return _rule_from_row(conn.execute(_RULE_SELECT + "WHERE r.id = %s", (rule_id,)).fetchone())

    def delete_rule(self, rule_id: int) -> bool:
        with self._pool.connection() as conn:
            return conn.execute(_DELETE_RULE, (rule_id,)).rowcount == 1

    @staticmethod
    def _rule_params(conn, rule: AlertRuleIn) -> dict:
        host_id = None
        if rule.host is not None:
            row = conn.execute(_HOST_ID, (rule.host,)).fetchone()
            if row is None:
                raise UnknownHostError(rule.host)
            host_id = row[0]
        return {
            "name": rule.name,
            "metric_name": rule.metric_name,
            "operator": rule.operator,
            "threshold": rule.threshold,
            "duration_seconds": rule.duration_seconds,
            "host_id": host_id,
            "tag_filter": Jsonb(rule.tag_filter),
            "enabled": rule.enabled,
        }

    def open_alerts(self) -> list[Alert]:
        with self._pool.connection() as conn:
            rows = conn.execute(_OPEN_ALERTS).fetchall()
        return [
            Alert(
                id=alert_id,
                rule_id=rule_id,
                host_id=host_id,
                hostname=hostname,
                unit=unit,
                tags=tags,
                rule_name=rule_name,
                metric_name=metric_name,
                operator=op,
                threshold=threshold,
                duration_seconds=duration,
                state=state,
                pending_since=float(pending_since),
                fired_at=_epoch(fired_at),
                resolved_at=_epoch(resolved_at),
                resolve_reason=reason,
                last_value=last_value,
                last_evaluated_at=float(evaluated_at),
            )
            for (
                alert_id, rule_id, host_id, hostname, unit, tags, rule_name, metric_name, op,
                threshold, duration, state, pending_since, fired_at, resolved_at, reason,
                last_value, evaluated_at,
            ) in rows
        ]

    def apply_alert_changes(self, changes: list[AlertChange]) -> list[AlertChange]:
        applied: list[AlertChange] = []
        with self._pool.connection() as conn, conn.transaction():
            for change in changes:
                if change.after is None:
                    conn.execute(_DELETE_ALERT, (change.before.id,))
                elif change.before is None:
                    (alert_id,) = conn.execute(_INSERT_ALERT, _alert_params(change.after)).fetchone()
                    change = replace(change, after=replace(change.after, id=alert_id))
                else:
                    conn.execute(_UPDATE_ALERT, _alert_params(change.after))
                applied.append(change)
        return applied


def _rule_from_row(row) -> AlertRule:
    rule_id, name, metric_name, op, threshold, duration, host, tag_filter, enabled, created, updated = row
    return AlertRule(
        id=rule_id,
        name=name,
        metric_name=metric_name,
        operator=op,
        threshold=threshold,
        duration_seconds=duration,
        host=host,
        tag_filter=tag_filter,
        enabled=enabled,
        created_at=float(created),
        updated_at=float(updated),
    )


def _epoch(value) -> float | None:
    return None if value is None else float(value)


def _alert_params(a: Alert) -> dict:
    return {
        "id": a.id,
        "rule_id": a.rule_id,
        "host_id": a.host_id,
        "unit": a.unit,
        "tags": Jsonb(a.tags),
        "rule_name": a.rule_name,
        "metric_name": a.metric_name,
        "operator": a.operator,
        "threshold": a.threshold,
        "duration_seconds": a.duration_seconds,
        "state": a.state,
        "pending_since": a.pending_since,
        "fired_at": a.fired_at,
        "resolved_at": a.resolved_at,
        "resolve_reason": a.resolve_reason,
        "last_value": a.last_value,
        "last_evaluated_at": a.last_evaluated_at,
    }
