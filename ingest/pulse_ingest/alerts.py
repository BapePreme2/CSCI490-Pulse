from __future__ import annotations

import json
import logging
import operator
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

import psycopg
from psycopg_pool import PoolTimeout

from pulse_ingest.store import STALE_AFTER_SECONDS, AlertStore, RuleSeries

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 10.0

# Same set as schemas.Operator and the alert_rules CHECK constraint.
OPERATORS: dict[str, Callable[[float, float], bool]] = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
}

SeriesKey = tuple[int, str, str, str]


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
        return (s.rule_id, s.hostname, s.unit, json.dumps(s.tags, sort_keys=True))


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
    """

    def __init__(
        self,
        store: AlertStore,
        handle: Callable[[list[Evaluation]], None],
        interval: float = DEFAULT_INTERVAL_SECONDS,
        max_age: float = STALE_AFTER_SECONDS,
        max_gap: float = STALE_AFTER_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if interval <= 0:
            raise ValueError("interval must be positive")
        self._store = store
        self._handle = handle
        self._interval = interval
        self._max_age = max_age
        self._max_gap = max_gap
        self._clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def evaluate_once(self) -> list[Evaluation]:
        # The window reaches max_gap past each rule's duration so that a run
        # which started right at the window's edge is still visible as one.
        series = self._store.rule_series(since=self._clock() - self._max_age, padding=self._max_gap)
        evaluations = evaluate(series, self._max_gap)
        self._handle(evaluations)
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
                logger.warning("Alert evaluation skipped, database unavailable: %s", exc)
            except Exception:
                logger.exception("Alert evaluation failed")
            self._stop.wait(self._interval)


class BreachLogger:
    """Logs when a series' verdict changes: OK, breaching, or sustained.

    A stand-in handler until the alert state machine (ALERT-04) consumes
    evaluations instead. Logs changes only, not every cycle.
    """

    def __init__(self) -> None:
        self._status: dict[SeriesKey, tuple[str, Evaluation]] = {}

    def __call__(self, evaluations: list[Evaluation]) -> None:
        current = {e.series_key: (_status(e), e) for e in evaluations}

        for key, (status, e) in current.items():
            previous = self._status.get(key, ("ok", None))[0]
            if status == previous:
                continue
            d = e.series.duration_seconds
            if status == "sustained":
                logger.info("Rule breach sustained for %ss: %s", d, _describe(e))
            elif status == "breaching" and previous == "ok":
                logger.info("Rule breaching, pending until sustained for %ss: %s", d, _describe(e))
            elif status == "breaching":
                # sustained -> breaching only happens if the rule's duration
                # was raised mid-breach; report it rather than stay silent.
                logger.info("Rule breach no longer sustained for %ss: %s", d, _describe(e))
            else:
                logger.info("Rule no longer breaching: %s", _describe(e))
        for key, (status, e) in self._status.items():
            if key not in current and status != "ok":
                logger.info(
                    "Rule no longer evaluated (no recent samples, or rule disabled/changed): %s",
                    _describe(e),
                )
        self._status = current


def _status(e: Evaluation) -> str:
    if e.sustained:
        return "sustained"
    return "breaching" if e.breaching else "ok"


def _describe(e: Evaluation) -> str:
    s = e.series
    latest = s.points[-1]
    tags = ",".join(f"{k}={v}" for k, v in sorted(s.tags.items()))
    series = f"{s.metric_name}{{{tags}}}" if tags else s.metric_name
    return (
        f"#{s.rule_id} {s.rule_name!r} on {s.hostname}: "
        f"{series} = {latest.value:g} {s.unit} ({s.operator} {s.threshold:g})"
    )
