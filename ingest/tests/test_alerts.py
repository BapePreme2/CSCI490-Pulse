import logging
import threading

import psycopg
import pytest

from pulse_ingest.alerts import OPERATORS, BreachLogger, RuleEvaluator, evaluate
from pulse_ingest.schemas import Operator
from pulse_ingest.store import STALE_AFTER_SECONDS, HistoryPoint, RuleSeries

NOW = 1789419042.0


def rule_series(*values, every=10.0, operator=">", threshold=80.0, duration=0, rule_id=1, hostname="web-1", **tags):
    """A series whose values are `every` seconds apart, the last one at NOW.

    A value of None leaves a gap: no sample at that slot.
    """
    count = len(values)
    points = [
        HistoryPoint(timestamp=NOW - (count - 1 - i) * every, value=v)
        for i, v in enumerate(values)
        if v is not None
    ]
    return RuleSeries(
        rule_id=rule_id,
        rule_name="High CPU",
        metric_name="cpu.usage",
        operator=operator,
        threshold=threshold,
        duration_seconds=duration,
        hostname=hostname,
        unit="percent",
        tags=tags,
        points=points,
    )


def verdict(series, max_gap=STALE_AFTER_SECONDS):
    (e,) = evaluate([series], max_gap)
    return e


class FakeAlertStore:
    def __init__(self, series=(), errors=()):
        self.series = list(series)
        self.errors = list(errors)
        self.calls = []
        self.called = threading.Event()

    def rule_series(self, since, padding):
        self.calls.append((since, padding))
        self.called.set()
        if self.errors:
            raise self.errors.pop(0)
        return self.series


def test_operators_match_the_schema():
    assert set(OPERATORS) == set(Operator.__args__)


@pytest.mark.parametrize(
    "operator, value, breaching",
    [
        (">", 80.0, False), (">", 80.1, True),
        (">=", 80.0, True), (">=", 79.9, False),
        ("<", 80.0, False), ("<", 79.9, True),
        ("<=", 80.0, True), ("<=", 80.1, False),
    ],
)
def test_latest_sample_is_compared_to_the_threshold(operator, value, breaching):
    assert verdict(rule_series(value, operator=operator)).breaching is breaching


def test_zero_duration_is_sustained_on_the_first_breaching_sample():
    e = verdict(rule_series(95.0, duration=0))

    assert (e.breaching, e.sustained, e.breaching_since) == (True, True, NOW)


def test_breach_shorter_than_the_duration_is_breaching_but_not_sustained():
    # 30 samples, 10s apart, all breaching: held for 290s of a 300s rule.
    e = verdict(rule_series(*[95.0] * 30, duration=300))

    assert (e.breaching, e.sustained) == (True, False)
    assert e.breaching_since == NOW - 290


def test_breach_that_holds_for_the_full_duration_is_sustained():
    e = verdict(rule_series(*[95.0] * 31, duration=300))

    assert (e.breaching, e.sustained, e.breaching_since) == (True, True, NOW - 300)


def test_a_dip_back_within_the_threshold_restarts_the_run():
    e = verdict(rule_series(*[95.0] * 20, 50.0, *[95.0] * 15, duration=300))

    assert (e.breaching, e.sustained, e.breaching_since) == (True, False, NOW - 140)


def test_a_latest_sample_within_the_threshold_is_ok_regardless_of_history():
    e = verdict(rule_series(*[95.0] * 40, 50.0, duration=300))

    assert (e.breaching, e.sustained, e.breaching_since) == (False, False, None)


def test_a_gap_longer_than_max_gap_breaks_the_run():
    # Samples every 10s with three missing slots: a 40s silence.
    e = verdict(rule_series(*[95.0] * 20, None, None, None, *[95.0] * 15, duration=300), max_gap=30)

    assert (e.sustained, e.breaching_since) == (False, NOW - 140)


def test_a_gap_of_exactly_max_gap_does_not_break_the_run():
    e = verdict(rule_series(*[95.0] * 20, None, None, *[95.0] * 15, duration=300), max_gap=30)

    assert (e.sustained, e.breaching_since) == (True, NOW - 360)


def test_a_run_reaching_the_start_of_the_window_is_sustained():
    # Everything fetched breaches: the run started at or before the window.
    e = verdict(rule_series(*[95.0] * 34, duration=300))

    assert e.sustained
    assert e.breaching_since == NOW - 330


def test_series_key_distinguishes_rule_host_and_tags_but_not_tag_order():
    def key(**kw):
        return verdict(rule_series(95.0, **kw)).series_key

    assert key(core="0", environment="dev") == key(environment="dev", core="0")
    assert len({key(), key(core="0"), key(hostname="web-2"), key(rule_id=2)}) == 4


def test_evaluate_once_asks_for_recent_series_with_gap_padding_and_hands_off_results():
    store = FakeAlertStore([rule_series(95.0), rule_series(10.0, core="0")])
    handled = []
    evaluator = RuleEvaluator(store, handled.append, max_age=30, max_gap=25, clock=lambda: NOW)

    results = evaluator.evaluate_once()

    assert store.calls == [(NOW - 30, 25)]
    assert handled == [results]
    assert [r.breaching for r in results] == [True, False]


def test_rejects_non_positive_interval():
    with pytest.raises(ValueError):
        RuleEvaluator(FakeAlertStore(), lambda _: None, interval=0)


def test_loop_survives_errors_and_keeps_evaluating(caplog):
    store = FakeAlertStore(errors=[psycopg.OperationalError("db down"), RuntimeError("bug")])
    handled = threading.Event()
    evaluator = RuleEvaluator(store, lambda _: handled.set(), interval=0.01)

    evaluator.start()
    try:
        assert handled.wait(5), "evaluator stopped after an error"
    finally:
        evaluator.stop()

    assert len(store.calls) >= 3
    messages = [r.getMessage() for r in caplog.records]
    assert any("database unavailable" in m for m in messages)
    assert any(m == "Alert evaluation failed" for m in messages)


def test_stop_ends_the_thread_without_waiting_out_the_interval():
    store = FakeAlertStore()
    evaluator = RuleEvaluator(store, lambda _: None, interval=60)

    evaluator.start()
    assert store.called.wait(5)
    thread = evaluator._thread
    evaluator.stop(timeout=2)

    assert not thread.is_alive()


def breach_log(caplog, *cycles):
    log = BreachLogger()
    with caplog.at_level(logging.INFO, logger="pulse_ingest.alerts"):
        for series in cycles:
            caplog.clear()
            log(evaluate(series))
            yield [r.getMessage() for r in caplog.records]


def test_breach_logger_reports_pending_then_sustained_once_each(caplog):
    pending, still_pending, sustained, still_sustained = breach_log(
        caplog,
        [rule_series(95.0, duration=20, core="0")],
        [rule_series(95.0, 96.0, duration=20, core="0")],
        [rule_series(95.0, 96.0, 97.0, duration=20, core="0")],
        [rule_series(95.0, 96.0, 97.0, 98.0, duration=20, core="0")],
    )

    assert pending == [
        "Rule breaching, pending until sustained for 20s: "
        "#1 'High CPU' on web-1: cpu.usage{core=0} = 95 percent (> 80)"
    ]
    assert still_pending == []
    assert sustained == [
        "Rule breach sustained for 20s: #1 'High CPU' on web-1: cpu.usage{core=0} = 97 percent (> 80)"
    ]
    assert still_sustained == []


def test_breach_logger_zero_duration_goes_straight_to_sustained(caplog):
    (first,) = breach_log(caplog, [rule_series(95.0)])

    assert first == ["Rule breach sustained for 0s: #1 'High CPU' on web-1: cpu.usage = 95 percent (> 80)"]


@pytest.mark.parametrize("duration", [0, 300])
def test_breach_logger_logs_recovery_from_pending_or_sustained(caplog, duration):
    _, recovered = breach_log(caplog, [rule_series(95.0, duration=duration)], [rule_series(40.0)])

    assert recovered == ["Rule no longer breaching: #1 'High CPU' on web-1: cpu.usage = 40 percent (> 80)"]


def test_breach_logger_reports_a_breach_that_stops_being_sustained(caplog):
    # e.g. the rule's duration was raised while it was already sustained.
    _, demoted = breach_log(caplog, [rule_series(95.0, duration=0)], [rule_series(95.0, duration=300)])

    assert len(demoted) == 1
    assert demoted[0].startswith("Rule breach no longer sustained for 300s")


def test_breach_logger_distinguishes_a_series_that_disappears(caplog):
    _, gone = breach_log(caplog, [rule_series(95.0)], [])

    assert len(gone) == 1
    assert gone[0].startswith("Rule no longer evaluated")


def test_breach_logger_ignores_series_that_never_breach(caplog):
    assert list(breach_log(caplog, [rule_series(10.0)], [rule_series(20.0)], [])) == [[], [], []]
