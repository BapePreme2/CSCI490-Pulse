import time

import psycopg
import pytest

from pulse_ingest.alerts import AlertTracker, RuleEvaluator
from pulse_ingest.migrate import apply_migrations
from pulse_ingest.schemas import AlertRuleIn, MetricBatch
from pulse_ingest.store import PostgresMetricStore, UnknownHostError

pytestmark = pytest.mark.integration


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


def rule(**overrides):
    data = {"name": "High CPU", "metric_name": "cpu.usage", "operator": ">", "threshold": 80, "duration_seconds": 0}
    data.update(overrides)
    return AlertRuleIn(**data)


def register(store, host="web-1", value=1.0, ts=None, **tags):
    store.write_batch(
        MetricBatch(
            host=host,
            metrics=[{"name": "cpu.usage", "unit": "percent", "value": value, "timestamp": ts or time.time(), "tags": tags}],
        )
    )


def test_no_rules_initially(store):
    assert store.list_rules() == []


def test_create_stores_every_field_and_reads_back_identically(store):
    register(store)

    created = store.create_rule(
        rule(host="web-1", tag_filter={"core": None, "mount": "/"}, enabled=False, threshold=92.5, duration_seconds=60)
    )

    assert (created.name, created.metric_name, created.operator, created.threshold, created.duration_seconds) == (
        "High CPU", "cpu.usage", ">", 92.5, 60
    )
    assert (created.host, created.tag_filter, created.enabled) == ("web-1", {"core": None, "mount": "/"}, False)
    assert created.created_at == pytest.approx(time.time(), abs=60)
    assert store.get_rule(created.id) == created
    assert store.list_rules() == [created]


def test_fleet_wide_rule_has_no_host(store, db_url):
    created = store.create_rule(rule())

    assert created.host is None
    assert query(db_url, "SELECT host_id FROM alert_rules") == [(None,)]


def test_rule_for_an_unknown_host_is_rejected_and_nothing_is_stored(store):
    with pytest.raises(UnknownHostError):
        store.create_rule(rule(host="ghost"))

    assert store.list_rules() == []


def test_update_replaces_fields_and_bumps_updated_at(store, db_url):
    register(store)
    created = store.create_rule(rule())
    query(db_url, "UPDATE alert_rules SET updated_at = updated_at - interval '1 hour' RETURNING id")
    backdated = store.get_rule(created.id).updated_at

    updated = store.update_rule(created.id, rule(name="Hot", host="web-1", threshold=95, enabled=False))

    assert (updated.id, updated.name, updated.host, updated.threshold, updated.enabled) == (
        created.id, "Hot", "web-1", 95.0, False
    )
    assert updated.created_at == created.created_at
    assert updated.updated_at > backdated + 3000
    assert store.get_rule(created.id) == updated


def test_update_to_an_unknown_host_changes_nothing(store):
    created = store.create_rule(rule())

    with pytest.raises(UnknownHostError):
        store.update_rule(created.id, rule(name="changed", host="ghost"))

    assert store.get_rule(created.id) == created


def test_update_and_get_of_a_missing_rule_return_none(store):
    assert store.update_rule(99, rule()) is None
    assert store.get_rule(99) is None


def test_delete_reports_whether_anything_was_deleted(store):
    created = store.create_rule(rule())

    assert store.delete_rule(created.id) is True
    assert store.delete_rule(created.id) is False
    assert store.list_rules() == []


def test_rules_are_listed_in_creation_order(store):
    ids = [store.create_rule(rule(name=n)).id for n in "abc"]

    assert [r.id for r in store.list_rules()] == ids


def test_a_rule_created_through_the_store_is_evaluated_and_fires(store, db_url):
    now = time.time()
    store.create_rule(rule(threshold=50, tag_filter={"core": None}))
    register(store, value=90.0, ts=now)
    register(store, value=10.0, ts=now, core="0")

    RuleEvaluator(store, AlertTracker(store), clock=lambda: now).evaluate_once()

    assert query(db_url, "SELECT state, tags FROM alerts") == [("firing", {})]


def test_deleting_a_rule_resolves_its_firing_alert_on_the_next_cycle(store, db_url):
    now = time.time()
    created = store.create_rule(rule(threshold=50))
    register(store, value=90.0, ts=now)
    evaluator = RuleEvaluator(store, AlertTracker(store), clock=lambda: now)
    evaluator.evaluate_once()

    store.delete_rule(created.id)
    evaluator.evaluate_once()

    assert query(db_url, "SELECT rule_id, rule_name, state, resolve_reason FROM alerts") == [
        (None, "High CPU", "resolved", "not_evaluated")
    ]
