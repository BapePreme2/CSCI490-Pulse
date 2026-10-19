import logging
import threading
from dataclasses import replace

import psycopg
import pytest

from pulse_ingest.alerts import OPERATORS, AlertTracker, RuleEvaluator, evaluate, plan_transitions
from pulse_ingest.schemas import Operator
from pulse_ingest.store import STALE_AFTER_SECONDS, Alert, HistoryPoint, RuleSeries

NOW = 1789419042.0


def rule_series(
    *values, every=10.0, operator=">", threshold=80.0, duration=0, rule_id=1, hostname="web-1", rule_name="High CPU",
    **tags,
):
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
        rule_name=rule_name,
        metric_name="cpu.usage",
        operator=operator,
        threshold=threshold,
        duration_seconds=duration,
        host_id=7,
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
    evaluator = RuleEvaluator(store, lambda *args: handled.append(args), max_age=30, max_gap=25, clock=lambda: NOW)

    results = evaluator.evaluate_once()

    assert store.calls == [(NOW - 30, 25)]
    assert handled == [(results, NOW)]
    assert [r.breaching for r in results] == [True, False]


def test_rejects_non_positive_interval():
    with pytest.raises(ValueError):
        RuleEvaluator(FakeAlertStore(), lambda *_: None, interval=0)


def test_loop_survives_errors_and_keeps_evaluating(caplog):
    store = FakeAlertStore(errors=[psycopg.OperationalError("db down"), RuntimeError("bug")])
    handled = threading.Event()
    evaluator = RuleEvaluator(store, lambda *_: handled.set(), interval=0.01)

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
    evaluator = RuleEvaluator(store, lambda *_: None, interval=60)

    evaluator.start()
    assert store.called.wait(5)
    thread = evaluator._thread
    evaluator.stop(timeout=2)

    assert not thread.is_alive()


# --- the alert state machine (ALERT-04) ---

LATER = NOW + 100


def alert(state="pending", alert_id=5, rule_id=1, pending_since=NOW - 50, fired_at=None, **tags):
    return Alert(
        id=alert_id,
        rule_id=rule_id,
        host_id=7,
        hostname="web-1",
        unit="percent",
        tags=tags,
        rule_name="High CPU",
        metric_name="cpu.usage",
        operator=">",
        threshold=80.0,
        duration_seconds=300,
        state=state,
        pending_since=pending_since,
        last_value=91.0,
        last_evaluated_at=NOW - 10,
        fired_at=fired_at if fired_at is not None or state != "firing" else NOW - 20,
    )


def step(open_alerts, *series, now=LATER):
    return plan_transitions(list(open_alerts), evaluate(series), now)


def pending_series(**kw):
    """Breaching for 100s of a 300s rule."""
    return rule_series(*[95.0] * 11, duration=300, **kw)


def sustained_series(**kw):
    return rule_series(*[95.0] * 31, duration=300, **kw)


def ok_series(**kw):
    return rule_series(*[95.0] * 10, 50.0, duration=300, **kw)


def test_nothing_happens_for_a_series_that_is_ok_with_no_alert():
    assert step([], ok_series()) == []


def test_a_new_breach_opens_a_pending_alert():
    (change,) = step([], pending_series())

    assert (change.kind, change.before) == ("pending", None)
    a = change.after
    assert (a.id, a.state, a.rule_id, a.host_id, a.hostname, a.unit, a.tags) == (
        None, "pending", 1, 7, "web-1", "percent", {}
    )
    assert (a.pending_since, a.fired_at, a.last_value, a.last_evaluated_at) == (NOW - 100, None, 95.0, LATER)
    assert (a.rule_name, a.metric_name, a.operator, a.threshold, a.duration_seconds) == (
        "High CPU", "cpu.usage", ">", 80.0, 300
    )


def test_an_already_sustained_breach_with_no_alert_fires_immediately():
    (change,) = step([], rule_series(95.0, duration=0))

    assert change.kind == "fired"
    assert (change.after.state, change.after.fired_at, change.after.pending_since) == ("firing", LATER, NOW)


def test_pending_alert_that_is_still_breaching_stays_pending_with_its_run_start_refreshed():
    before = alert("pending", pending_since=NOW - 300)

    (change,) = step([before], pending_series())

    assert change.kind == "updated"
    assert (change.after.id, change.after.state, change.after.pending_since) == (5, "pending", NOW - 100)
    assert (change.after.last_value, change.after.last_evaluated_at) == (95.0, LATER)


def test_pending_alert_fires_once_sustained():
    (change,) = step([alert("pending")], sustained_series())

    assert (change.kind, change.before.state) == ("fired", "pending")
    assert (change.after.id, change.after.state, change.after.fired_at) == (5, "firing", LATER)


@pytest.mark.parametrize("series", [[ok_series()], []], ids=["recovered", "not-evaluated"])
def test_pending_alert_is_cancelled_if_it_ends_before_firing(series):
    before = alert("pending")

    (change,) = step([before], *series)

    assert (change.kind, change.before, change.after) == ("cancelled", before, None)


def test_firing_alert_stays_firing_while_breaching_even_if_the_run_restarted():
    before = alert("firing", fired_at=NOW - 20, pending_since=NOW - 400)

    (change,) = step([before], pending_series())

    assert change.kind == "updated"
    assert (change.after.state, change.after.fired_at, change.after.pending_since) == ("firing", NOW - 20, NOW - 400)


def test_firing_alert_resolves_as_recovered_when_back_within_threshold():
    (change,) = step([alert("firing")], ok_series())

    assert change.kind == "resolved"
    a = change.after
    assert (a.id, a.state, a.resolved_at, a.resolve_reason, a.last_value) == (5, "resolved", LATER, "recovered", 50.0)


def test_firing_alert_resolves_as_not_evaluated_when_its_series_disappears():
    (change,) = step([alert("firing")])

    a = change.after
    assert (change.kind, a.state, a.resolved_at, a.resolve_reason) == ("resolved", "resolved", LATER, "not_evaluated")
    assert a.last_value == 91.0, "keeps the last value actually seen"


def test_open_alerts_reflect_the_rules_current_condition():
    (change,) = step([alert("pending")], rule_series(*[95.0] * 11, duration=600, threshold=90.0, rule_name="Hot"))

    a = change.after
    assert (a.rule_name, a.threshold, a.duration_seconds) == ("Hot", 90.0, 600)


def test_alerts_whose_rule_was_deleted_are_all_closed_even_on_the_same_series():
    firing = alert("firing", alert_id=1, rule_id=None)
    pending = alert("pending", alert_id=2, rule_id=None)
    other_firing = alert("firing", alert_id=3, rule_id=None)

    changes = step([firing, pending, other_firing])

    assert sorted((c.before.id, c.kind) for c in changes) == [(1, "resolved"), (2, "cancelled"), (3, "resolved")]


def test_each_series_has_its_own_alert():
    open_alerts = [alert("firing", alert_id=1, core="0"), alert("pending", alert_id=2, core="1")]

    changes = step(open_alerts, ok_series(core="0"), sustained_series(core="1"), pending_series(core="2"))

    assert sorted((c.kind, c.after.tags["core"]) for c in changes) == [
        ("fired", "1"), ("pending", "2"), ("resolved", "0"),
    ]


def test_alert_matches_its_series_regardless_of_tag_order():
    before = alert("pending", core="0", environment="dev")

    (change,) = step([before], pending_series(environment="dev", core="0"))

    assert change.kind == "updated"


class FakeAlertTrackerStore:
    def __init__(self, open_alerts=()):
        self.open = list(open_alerts)
        self.applied = []

    def open_alerts(self):
        return self.open

    def apply_alert_changes(self, changes):
        self.applied.append(changes)
        return [
            c if c.after is None or c.after.id is not None else type(c)(c.kind, c.before, replace(c.after, id=42))
            for c in changes
        ]


def track(caplog, store, *series, now=LATER):
    with caplog.at_level(logging.INFO, logger="pulse_ingest.alerts"):
        changes = AlertTracker(store)(evaluate(series), now)
    return changes, [r.getMessage() for r in caplog.records]


def test_tracker_persists_changes_and_logs_with_the_new_id(caplog):
    store = FakeAlertTrackerStore()

    changes, messages = track(caplog, store, sustained_series(core="0"))

    ((sent,),) = store.applied
    assert (sent.kind, sent.before, sent.after.id) == ("fired", None, None)
    assert changes[0].after.id == 42
    assert messages == ["Alert 42 FIRING: #1 'High CPU' on web-1: cpu.usage{core=0} = 95 percent (> 80)"]


def test_tracker_logs_pending_resolved_and_cancelled(caplog):
    store = FakeAlertTrackerStore([alert("firing", alert_id=1, fired_at=LATER - 90), alert("pending", alert_id=2, core="1")])

    _, messages = track(caplog, store, ok_series(), pending_series(core="2"))

    assert sorted(messages) == [
        "Alert 1 resolved (recovered) after 90s firing: #1 'High CPU' on web-1: cpu.usage = 50 percent (> 80)",
        "Alert 2 cancelled, recovered before firing: #1 'High CPU' on web-1: cpu.usage{core=1} = 91 percent (> 80)",
        "Alert 42 pending, fires if sustained for 300s: #1 'High CPU' on web-1: cpu.usage{core=2} = 95 percent (> 80)",
    ]


def test_tracker_writes_nothing_when_nothing_is_open_or_breaching(caplog):
    store = FakeAlertTrackerStore()

    changes, messages = track(caplog, store, ok_series())

    assert (changes, messages, store.applied) == ([], [], [])


def test_updates_are_persisted_but_not_logged(caplog):
    store = FakeAlertTrackerStore([alert("firing")])

    changes, messages = track(caplog, store, sustained_series())

    assert [c.kind for c in changes] == ["updated"]
    assert messages == []
