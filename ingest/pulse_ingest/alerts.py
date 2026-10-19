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

from pulse_ingest.store import STALE_AFTER_SECONDS, AlertStore, RuleSample

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
    sample: RuleSample
    breaching: bool

    @property
    def series_key(self) -> SeriesKey:
        """Identifies one (rule, series) pair across evaluation cycles."""
        s = self.sample
        return (s.rule_id, s.hostname, s.unit, json.dumps(s.tags, sort_keys=True))


def evaluate(samples: Iterable[RuleSample]) -> list[Evaluation]:
    return [Evaluation(s, OPERATORS[s.operator](s.value, s.threshold)) for s in samples]


class RuleEvaluator:
    """Evaluates every enabled rule on a fixed interval in a background thread.

    Each cycle compares the latest sample of each matching series against its
    rule's threshold. Only samples newer than `max_age` count, the same window
    that marks a host offline, so a host that stops reporting stops being
    evaluated instead of being judged forever on its last value.
    """

    def __init__(
        self,
        store: AlertStore,
        handle: Callable[[list[Evaluation]], None],
        interval: float = DEFAULT_INTERVAL_SECONDS,
        max_age: float = STALE_AFTER_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if interval <= 0:
            raise ValueError("interval must be positive")
        self._store = store
        self._handle = handle
        self._interval = interval
        self._max_age = max_age
        self._clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def evaluate_once(self) -> list[Evaluation]:
        evaluations = evaluate(self._store.latest_rule_samples(since=self._clock() - self._max_age))
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
    """Logs when a series starts or stops breaching its rule.

    A stand-in handler until the alert state machine (ALERT-04) consumes
    evaluations instead. Logs changes only, not every cycle.
    """

    def __init__(self) -> None:
        self._breaching: dict[SeriesKey, Evaluation] = {}

    def __call__(self, evaluations: list[Evaluation]) -> None:
        current = {e.series_key: e for e in evaluations}
        breaching = {k: e for k, e in current.items() if e.breaching}

        for key, e in breaching.items():
            if key not in self._breaching:
                logger.info("Rule breaching: %s", _describe(e))
        for key, e in self._breaching.items():
            if key in breaching:
                continue
            if key in current:
                logger.info("Rule no longer breaching: %s", _describe(current[key]))
            else:
                logger.info(
                    "Rule no longer evaluated (no recent samples, or rule disabled/changed): %s",
                    _describe(e),
                )
        self._breaching = breaching


def _describe(e: Evaluation) -> str:
    s = e.sample
    tags = ",".join(f"{k}={v}" for k, v in sorted(s.tags.items()))
    series = f"{s.metric_name}{{{tags}}}" if tags else s.metric_name
    return (
        f"#{s.rule_id} {s.rule_name!r} on {s.hostname}: "
        f"{series} = {s.value:g} {s.unit} ({s.operator} {s.threshold:g})"
    )
