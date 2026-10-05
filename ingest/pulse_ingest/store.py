from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from pulse_ingest.schemas import MetricBatch

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

_LIST_HOSTS = """
SELECT hostname, extract(epoch FROM first_seen_at), extract(epoch FROM last_seen_at)
FROM hosts
ORDER BY last_seen_at DESC
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


@dataclass(frozen=True)
class HistoryPoint:
    timestamp: float
    value: float


@dataclass(frozen=True)
class HistorySeries:
    unit: str
    tags: dict[str, str]
    points: list[HistoryPoint] = field(default_factory=list)


class MetricStore(Protocol):
    def write_batch(self, batch: MetricBatch) -> WriteResult:
        """Persist a batch atomically; already-stored samples are skipped."""


class QueryStore(Protocol):
    def host_exists(self, host: str) -> bool: ...

    def list_hosts(self) -> list[HostSummary]:
        """Every known host, most recently active first."""

    def get_latest(self, host: str) -> list[LatestMetric]:
        """The most recent value of every series (metric + tags) a host reports."""

    def get_history(self, host: str, name: str, start: float, end: float) -> list[HistorySeries]:
        """All samples for one metric name in [start, end], grouped into series by tags."""


class Store(MetricStore, QueryStore, Protocol):
    """Everything the API needs from a storage backend."""


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

    def list_hosts(self) -> list[HostSummary]:
        with self._pool.connection() as conn:
            rows = conn.execute(_LIST_HOSTS).fetchall()
        return [
            HostSummary(hostname=hostname, first_seen_at=float(first), last_seen_at=float(last))
            for hostname, first, last in rows
        ]

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
