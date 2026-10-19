import logging
import threading

import psycopg
import pytest

from pulse_ingest.alerts import OPERATORS, BreachLogger, RuleEvaluator, evaluate
from pulse_ingest.schemas import Operator
from pulse_ingest.store import STALE_AFTER_SECONDS, RuleSample

NOW = 1789419042.0


def sample(value=90.0, operator=">", threshold=80.0, rule_id=1, hostname="web-1", **tags):
    return RuleSample(
        rule_id=rule_id,
        rule_name="High CPU",
        metric_name="cpu.usage",
        operator=operator,
        threshold=threshold,
        hostname=hostname,
        unit="percent",
        tags=tags,
        value=value,
        timestamp=NOW,
    )


class FakeAlertStore:
    def __init__(self, samples=(), errors=()):
        self.samples = list(samples)
        self.errors = list(errors)
        self.calls = []
        self.called = threading.Event()

    def latest_rule_samples(self, since):
        self.calls.append(since)
        self.called.set()
        if self.errors:
            raise self.errors.pop(0)
        return self.samples


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
def test_evaluate_compares_value_to_threshold(operator, value, breaching):
    (result,) = evaluate([sample(value=value, operator=operator, threshold=80.0)])

    assert result.breaching is breaching


def test_series_key_distinguishes_rule_host_and_tags_but_not_tag_order():
    def key(**kw):
        return evaluate([sample(**kw)])[0].series_key

    assert key(core="0", environment="dev") == key(environment="dev", core="0")
    assert len({key(), key(core="0"), key(hostname="web-2"), key(rule_id=2)}) == 4


def test_evaluate_once_only_asks_for_recent_samples_and_hands_off_results():
    store = FakeAlertStore([sample(value=95.0), sample(value=10.0, core="0")])
    handled = []
    evaluator = RuleEvaluator(store, handled.append, clock=lambda: NOW)

    results = evaluator.evaluate_once()

    assert store.calls == [NOW - STALE_AFTER_SECONDS]
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
        for samples in cycles:
            caplog.clear()
            log(evaluate(samples))
            yield [r.getMessage() for r in caplog.records]


def test_breach_logger_logs_once_when_a_series_starts_breaching(caplog):
    first, second = breach_log(caplog, [sample(value=95.0, core="0")], [sample(value=97.0, core="0")])

    assert first == ["Rule breaching: #1 'High CPU' on web-1: cpu.usage{core=0} = 95 percent (> 80)"]
    assert second == []


def test_breach_logger_logs_recovery(caplog):
    _, recovered = breach_log(caplog, [sample(value=95.0)], [sample(value=40.0)])

    assert recovered == ["Rule no longer breaching: #1 'High CPU' on web-1: cpu.usage = 40 percent (> 80)"]


def test_breach_logger_distinguishes_a_series_that_disappears(caplog):
    _, gone = breach_log(caplog, [sample(value=95.0)], [])

    assert len(gone) == 1
    assert gone[0].startswith("Rule no longer evaluated")


def test_breach_logger_ignores_series_that_never_breach(caplog):
    assert list(breach_log(caplog, [sample(value=10.0)], [sample(value=20.0)], [])) == [[], [], []]
