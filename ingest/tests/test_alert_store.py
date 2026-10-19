"""The alert state machine against real Postgres: persistence and full cycles."""

import psycopg
import pytest

from pulse_ingest.alerts import AlertTracker, RuleEvaluator
from pulse_ingest.migrate import apply_migrations
from pulse_ingest.schemas import MetricBatch
from pulse_ingest.store import Alert, AlertChange, PostgresMetricStore

pytestmark = pytest.mark.integration

TS = 1789419042.0


@pytest.fixture
def db_url(scratch_db_url):
    apply_migrations(scratch_db_url)
    return scratch_db_url


@pytest.fixture
def store(db_url):
    s = PostgresMetricStore(db_url, min_size=1, max_size=2)
    s.open()
    yield s
    s.close()


def query(db_url, sql, params=()):
    with psycopg.connect(db_url) as conn:
        return conn.execute(sql, params).fetchall()


def report(store, value, ts, host="web-1", **tags):
    store.write_batch(
        MetricBatch(
            host=host,
            metrics=[{"name": "cpu.usage", "unit": "percent", "value": value, "timestamp": ts, "tags": tags}],
        )
    )


def add_rule(db_url, duration=30, threshold=80.0):
    return query(
        db_url,
        "INSERT INTO alert_rules (name, metric_name, operator, threshold, duration_seconds) "
        "VALUES ('High CPU', 'cpu.usage', '>', %s, %s) RETURNING id",
        (threshold, duration),
    )[0][0]


def host_id(db_url, host="web-1"):
    return query(db_url, "SELECT id FROM hosts WHERE hostname = %s", (host,))[0][0]


def new_alert(db_url, rule_id, **overrides):
    fields = dict(
        id=None, rule_id=rule_id, host_id=host_id(db_url), hostname="web-1", unit="percent",
        tags={"core": "0"}, rule_name="High CPU", metric_name="cpu.usage", operator=">",
        threshold=80.0, duration_seconds=30, state="pending", pending_since=TS - 20,
        last_value=91.5, last_evaluated_at=TS,
    )
    fields.update(overrides)
    return Alert(**fields)


def test_inserted_alert_round_trips_through_open_alerts(store, db_url):
    report(store, 91.5, TS, core="0")
    rule_id = add_rule(db_url)

    (applied,) = store.apply_alert_changes([AlertChange("pending", None, new_alert(db_url, rule_id))])

    assert applied.after.id is not None
    assert store.open_alerts() == [applied.after]


def test_update_and_delete_apply_to_the_right_row(store, db_url):
    report(store, 91.5, TS, core="0")
    rule_id = add_rule(db_url)
    (a,) = store.apply_alert_changes([AlertChange("pending", None, new_alert(db_url, rule_id))])
    (b,) = store.apply_alert_changes(
        [AlertChange("pending", None, new_alert(db_url, rule_id, tags={"core": "1"}))]
    )
    fired = new_alert(db_url, rule_id, id=a.after.id, state="firing", fired_at=TS + 10, last_value=99.0)

    store.apply_alert_changes([AlertChange("fired", a.after, fired), AlertChange("cancelled", b.after, None)])

    assert store.open_alerts() == [fired]


def test_resolved_alerts_are_not_open(store, db_url):
    report(store, 91.5, TS, core="0")
    rule_id = add_rule(db_url)
    (a,) = store.apply_alert_changes(
        [AlertChange("fired", None, new_alert(db_url, rule_id, state="firing", fired_at=TS))]
    )
    resolved = new_alert(
        db_url, rule_id, id=a.after.id, state="resolved", fired_at=TS, resolved_at=TS + 60,
        resolve_reason="recovered",
    )

    store.apply_alert_changes([AlertChange("resolved", a.after, resolved)])

    assert store.open_alerts() == []
    assert query(db_url, "SELECT state, resolve_reason FROM alerts") == [("resolved", "recovered")]


def test_a_failing_change_rolls_back_the_whole_cycle(store, db_url):
    report(store, 91.5, TS, core="0")
    rule_id = add_rule(db_url)
    ok = AlertChange("pending", None, new_alert(db_url, rule_id))
    duplicate_open = AlertChange("pending", None, new_alert(db_url, rule_id))

    with pytest.raises(psycopg.errors.UniqueViolation):
        store.apply_alert_changes([ok, duplicate_open])

    assert store.open_alerts() == []


def test_open_alert_survives_its_rule_being_deleted(store, db_url):
    report(store, 91.5, TS, core="0")
    rule_id = add_rule(db_url)
    store.apply_alert_changes([AlertChange("fired", None, new_alert(db_url, rule_id, state="firing", fired_at=TS))])
    query(db_url, "DELETE FROM alert_rules WHERE id = %s RETURNING id", (rule_id,))

    (a,) = store.open_alerts()

    assert (a.rule_id, a.rule_name, a.state) == (None, "High CPU", "firing")


# --- full cycles: real samples -> RuleEvaluator -> AlertTracker -> Postgres ---


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def alert_rows(db_url):
    return query(
        db_url,
        "SELECT state, extract(epoch FROM pending_since), extract(epoch FROM fired_at), "
        "extract(epoch FROM resolved_at), resolve_reason, last_value FROM alerts ORDER BY id",
    )


def run_cycles(store, db_url, clock, samples, tracker=None):
    """Report each (offset, value) sample, then evaluate once at that time."""
    tracker = tracker or AlertTracker(store)
    evaluator = RuleEvaluator(store, tracker, clock=clock)
    for offset, value in samples:
        clock.now = TS + offset
        if value is not None:
            report(store, value, clock.now)
        evaluator.evaluate_once()


def test_full_lifecycle_pending_firing_resolved(store, db_url):
    add_rule(db_url, duration=30)
    clock = Clock(TS)

    run_cycles(store, db_url, clock, [(0, 95.0), (10, 96.0), (20, 97.0)])
    assert alert_rows(db_url) == [("pending", TS, None, None, None, 97.0)]

    run_cycles(store, db_url, clock, [(30, 98.0)])
    assert alert_rows(db_url) == [("firing", TS, TS + 30, None, None, 98.0)]

    run_cycles(store, db_url, clock, [(40, 99.0), (50, 40.0)])
    assert alert_rows(db_url) == [("resolved", TS, TS + 30, TS + 50, "recovered", 40.0)]


def test_a_breach_that_ends_before_its_duration_leaves_no_history(store, db_url):
    add_rule(db_url, duration=30)
    clock = Clock(TS)

    run_cycles(store, db_url, clock, [(0, 95.0), (10, 96.0), (20, 50.0)])

    assert alert_rows(db_url) == []


def test_a_host_that_stops_reporting_resolves_as_not_evaluated(store, db_url):
    add_rule(db_url, duration=0)
    clock = Clock(TS)

    # Fires at 0; silent after 10; at 41 the last sample is over 30s old.
    run_cycles(store, db_url, clock, [(0, 95.0), (10, 95.0), (30, None), (41, None)])

    assert alert_rows(db_url) == [("resolved", TS, TS, TS + 41, "not_evaluated", 95.0)]


def test_a_restarted_api_continues_open_alerts_instead_of_refiring(store, db_url):
    add_rule(db_url, duration=0)
    clock = Clock(TS)
    run_cycles(store, db_url, clock, [(0, 95.0)])

    # A fresh tracker and evaluator, as after an API restart.
    run_cycles(store, db_url, clock, [(10, 96.0)], tracker=AlertTracker(store))

    assert alert_rows(db_url) == [("firing", TS, TS, None, None, 96.0)]


def test_a_new_breach_after_resolving_opens_a_new_alert(store, db_url):
    add_rule(db_url, duration=0)
    clock = Clock(TS)

    run_cycles(store, db_url, clock, [(0, 95.0), (10, 40.0), (20, 95.0)])

    assert [row[0] for row in alert_rows(db_url)] == ["resolved", "firing"]
