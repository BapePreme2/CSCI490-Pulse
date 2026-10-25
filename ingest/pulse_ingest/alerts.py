from __future__ import annotations

import json
import logging
import operator
import threading
import time
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass, replace

import psycopg
from psycopg_pool import PoolTimeout

from pulse_ingest.store import STALE_AFTER_SECONDS, Alert, AlertChange, AlertStore, RuleSeries

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 10.0

# How long after the API starts, or the database comes back, a series with
# no recent data is given the benefit of the doubt. While the API couldn't
# take writes, agents kept collecting and backing off: up to 60s between
# retries (agent/pulse_agent/buffer.py), then they deliver on their next
# cycle. Until that backlog has had time to land, missing data means the
# API was down, not that the host went quiet.
CATCH_UP_SECONDS = 90.0

# Same set as schemas.Operator and the alert_rules CHECK constraint.
OPERATORS: dict[str, Callable[[float, float], bool]] = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
}

# (rule id, hostname, unit, tags as sorted JSON). The rule id is None for an
# alert whose rule has since been deleted.
SeriesKey = tuple[int | None, str, str, str]


def _series_key(rule_id: int | None, hostname: str, unit: str, tags: dict[str, str]) -> SeriesKey:
    return (rule_id, hostname, unit, json.dumps(tags, sort_keys=True))


@dataclass(frozen=True)
class Evaluation:
    """One rule's verdict on one series.

    `breaching`: the latest sample is past the threshold. `sustained`: it has
    stayed past it, without a break, for the rule's full duration_seconds.
    `breaching_since`: when the current unbroken run started, as far back as
    the fetched window reaches (so "at least since" once it's sustained).
    """

    series: RuleSeries
    breaching: bool
    sustained: bool = False
    breaching_since: float | None = None

    @property
    def series_key(self) -> SeriesKey:
        """Identifies one (rule, series) pair across evaluation cycles."""
        s = self.series
        return _series_key(s.rule_id, s.hostname, s.unit, s.tags)


def evaluate(series: Iterable[RuleSeries], max_gap: float = STALE_AFTER_SECONDS) -> list[Evaluation]:
    return [_evaluate(s, max_gap) for s in series]


def _evaluate(s: RuleSeries, max_gap: float) -> Evaluation:
    breaches = OPERATORS[s.operator]
    latest = s.points[-1]
    if not breaches(latest.value, s.threshold):
        return Evaluation(s, breaching=False)

    # Walk back from the latest sample through the unbroken run of breaching
    # samples. A sample back within the threshold ends the run, and so does a
    # silence longer than max_gap: with no data in between there's no telling
    # whether the breach held through it.
    run_start = latest.timestamp
    for point in reversed(s.points[:-1]):
        if run_start - point.timestamp > max_gap or not breaches(point.value, s.threshold):
            break
        run_start = point.timestamp

    return Evaluation(
        s,
        breaching=True,
        sustained=latest.timestamp - run_start >= s.duration_seconds,
        breaching_since=run_start,
    )


class RuleEvaluator:
    """Evaluates every enabled rule on a fixed interval in a background thread.

    Each cycle checks every matching series against its rule: is the latest
    sample past the threshold, and has it stayed there for duration_seconds?
    Only series that reported within `max_age` are evaluated, the same window
    that marks a host offline, so a host that stops reporting stops being
    evaluated instead of being judged forever on its last value. A gap longer
    than `max_gap` between samples breaks a breaching run.

    Each cycle calls `handle(evaluations, now, catching_up)`. `catching_up`
    is true for the first `catch_up` seconds after the evaluator starts or a
    cycle fails for lack of a database, while agents' buffered data may
    still be arriving (see CATCH_UP_SECONDS).
    """

    def __init__(
        self,
        store: AlertStore,
        handle: Callable[[list[Evaluation], float, bool], object],
        interval: float = DEFAULT_INTERVAL_SECONDS,
        max_age: float = STALE_AFTER_SECONDS,
        max_gap: float = STALE_AFTER_SECONDS,
        catch_up: float = CATCH_UP_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if interval <= 0:
            raise ValueError("interval must be positive")
        self._store = store
        self._handle = handle
        self._interval = interval
        self._max_age = max_age
        self._max_gap = max_gap
        self._catch_up = catch_up
        self._clock = clock
        # Start of the current run of cycles with a working database; None
        # until the next cycle, after starting or after the database failed.
        self._receiving_since: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def evaluate_once(self) -> list[Evaluation]:
        # The window reaches max_gap past each rule's duration so that a run
        # which started right at the window's edge is still visible as one.
        now = self._clock()
        if self._receiving_since is None:
            self._receiving_since = now
        series = self._store.rule_series(since=now - self._max_age, padding=self._max_gap)
        evaluations = evaluate(series, self._max_gap)
        self._handle(evaluations, now, now - self._receiving_since < self._catch_up)
        return evaluations

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="pulse-alert-evaluator", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.evaluate_once()
            except (psycopg.OperationalError, PoolTimeout) as exc:
                # Expected while the database is down; no traceback every cycle.
                # Agents couldn't write meanwhile either, so catch up again.
                self._receiving_since = None
                logger.warning("Alert evaluation skipped, database unavailable: %s", exc)
            except Exception:
                logger.exception("Alert evaluation failed")
            self._stop.wait(self._interval)


def plan_transitions(
    open_alerts: list[Alert], evaluations: list[Evaluation], now: float, hold_rule_ids: Collection[int] = ()
) -> list[AlertChange]:
    """The alert state machine: what to do with each open alert and series.

    - no alert + breaching: open one, `pending`, or straight to `firing` if
      it's already sustained (e.g. duration_seconds = 0).
    - `pending` + sustained: `firing`.
    - `pending` + no longer breaching, or not evaluated: deleted
      ("cancelled"); it never fired, so it isn't history.
    - `firing` + still breaching: stays `firing`, even if the run restarted.
      The duration only gates pending -> firing, so a brief dip can't make a
      firing alert flap between resolved and firing.
    - `firing` + no longer breaching: `resolved` as `recovered`.
    - `firing` + not evaluated this cycle (series stopped reporting, or the
      rule was disabled, changed, or deleted): `resolved` as `not_evaluated`.
    - not evaluated, but its rule is in `hold_rule_ids`: left as it is. The
      tracker holds enabled rules' alerts this way while catching up, when
      missing data may be data that hasn't arrived yet.

    Every open alert that stays open is refreshed ("updated") with the
    latest value and the rule's current condition.
    """
    # An alert whose rule was deleted (rule_id NULL) can't match any
    # evaluation, and several such alerts could share a series key, so they
    # are kept out of the lookup and closed below.
    remaining = {
        _series_key(a.rule_id, a.hostname, a.unit, a.tags): a for a in open_alerts if a.rule_id is not None
    }
    orphaned = [a for a in open_alerts if a.rule_id is None]
    changes: list[AlertChange] = []

    for e in evaluations:
        alert = remaining.pop(e.series_key, None)
        change = _step(alert, e, now)
        if change is not None:
            changes.append(change)

    for alert in [*remaining.values(), *orphaned]:
        if alert.rule_id in hold_rule_ids:
            continue
        if alert.state == "pending":
            changes.append(AlertChange("cancelled", alert, None))
        else:
            resolved = replace(
                alert, state="resolved", resolved_at=now, resolve_reason="not_evaluated", last_evaluated_at=now
            )
            changes.append(AlertChange("resolved", alert, resolved))
    return changes


def _step(alert: Alert | None, e: Evaluation, now: float) -> AlertChange | None:
    if alert is None:
        if e.sustained:
            return AlertChange("fired", None, _new_alert(e, now, "firing"))
        if e.breaching:
            return AlertChange("pending", None, _new_alert(e, now, "pending"))
        return None

    if alert.state == "pending":
        if e.sustained:
            return AlertChange("fired", alert, _refresh(alert, e, now, state="firing", fired_at=now))
        if e.breaching:
            return AlertChange("updated", alert, _refresh(alert, e, now, pending_since=e.breaching_since))
        return AlertChange("cancelled", alert, None)

    # firing
    if e.breaching:
        return AlertChange("updated", alert, _refresh(alert, e, now))
    return AlertChange(
        "resolved", alert, _refresh(alert, e, now, state="resolved", resolved_at=now, resolve_reason="recovered")
    )


def _new_alert(e: Evaluation, now: float, state: str) -> Alert:
    s = e.series
    return Alert(
        id=None,
        rule_id=s.rule_id,
        host_id=s.host_id,
        hostname=s.hostname,
        unit=s.unit,
        tags=s.tags,
        rule_name=s.rule_name,
        metric_name=s.metric_name,
        operator=s.operator,
        threshold=s.threshold,
        duration_seconds=s.duration_seconds,
        state=state,
        pending_since=e.breaching_since,
        last_value=s.points[-1].value,
        last_evaluated_at=now,
        fired_at=now if state == "firing" else None,
    )


def _refresh(alert: Alert, e: Evaluation, now: float, **changes) -> Alert:
    s = e.series
    return replace(
        alert,
        rule_name=s.rule_name,
        metric_name=s.metric_name,
        operator=s.operator,
        threshold=s.threshold,
        duration_seconds=s.duration_seconds,
        last_value=s.points[-1].value,
        last_evaluated_at=now,
        **changes,
    )


class AlertTracker:
    """The evaluator's handler: runs the state machine, persists it, and
    passes each stored change to `notify` (see notify.Notifier).

    Open alerts live in Postgres, not memory, so an API restart picks up
    where it left off instead of re-firing everything. While the evaluator
    is catching up, an enabled rule's alert that wasn't evaluated is held
    rather than resolved, so the gap the restart itself caused doesn't
    resolve it as "not evaluated" just before its buffered data arrives and
    fires it again. A disabled or deleted rule's alerts close as usual,
    since that isn't a data gap.
    """

    def __init__(self, store: AlertStore, notify: Callable[[AlertChange], object] | None = None) -> None:
        self._store = store
        self._notify = notify

    def __call__(self, evaluations: list[Evaluation], now: float, catching_up: bool = False) -> list[AlertChange]:
        hold = self._store.enabled_rule_ids() if catching_up else ()
        changes = plan_transitions(self._store.open_alerts(), evaluations, now, hold)
        if not changes:
            return []
        applied = self._store.apply_alert_changes(changes)
        for change in applied:
            _log_change(change)
            # Only after the changes are committed, so nothing is announced
            # that didn't happen; the notifier picks which kinds to send.
            if self._notify is not None:
                self._notify(change)
        return applied


def _log_change(change: AlertChange) -> None:
    if change.kind == "pending":
        a = change.after
        logger.info("Alert %d pending, fires if sustained for %ss: %s", a.id, a.duration_seconds, _describe(a))
    elif change.kind == "fired":
        a = change.after
        logger.warning("Alert %d FIRING: %s", a.id, _describe(a))
    elif change.kind == "resolved":
        a = change.after
        logger.info(
            "Alert %d resolved (%s) after %ss firing: %s",
            a.id, a.resolve_reason, f"{a.resolved_at - a.fired_at:g}", _describe(a),
        )
    elif change.kind == "cancelled":
        a = change.before
        logger.info("Alert %d cancelled, recovered before firing: %s", a.id, _describe(a))


def series_label(a: Alert) -> str:
    """The alert's series as "cpu.usage{core=0}", or just the metric name."""
    tags = ",".join(f"{k}={v}" for k, v in sorted(a.tags.items()))
    return f"{a.metric_name}{{{tags}}}" if tags else a.metric_name


def _describe(a: Alert) -> str:
    rule = f"#{a.rule_id}" if a.rule_id is not None else "(deleted rule)"
    return (
        f"{rule} {a.rule_name!r} on {a.hostname}: "
        f"{series_label(a)} = {a.last_value:g} {a.unit} ({a.operator} {a.threshold:g})"
    )
