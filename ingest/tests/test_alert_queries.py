import psycopg
import pytest
from psycopg.types.json import Jsonb

from pulse_ingest.migrate import apply_migrations
from pulse_ingest.schemas import MetricBatch
from pulse_ingest.store import PostgresMetricStore

pytestmark = pytest.mark.integration

TS = 1789419042.0
SINCE = TS - 30


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


def metric(name="cpu.usage", unit="percent", value=1.0, ts=TS, **tags):
    return {"name": name, "unit": unit, "value": value, "timestamp": ts, "tags": tags}


def write(store, host, *metrics):
    store.write_batch(MetricBatch(host=host, metrics=list(metrics)))


def add_rule(db_url, metric_name="cpu.usage", host=None, tag_filter=None, enabled=True, threshold=80.0):
    with psycopg.connect(db_url) as conn:
        return conn.execute(
            "INSERT INTO alert_rules (name, metric_name, operator, threshold, duration_seconds, "
            "host_id, tag_filter, enabled) "
            "VALUES ('rule', %s, '>', %s, 0, (SELECT id FROM hosts WHERE hostname = %s), %s, %s) "
            "RETURNING id",
            (metric_name, threshold, host, Jsonb(tag_filter or {}), enabled),
        ).fetchone()[0]


def series(samples):
    return sorted((s.rule_id, s.hostname, tuple(sorted(s.tags.items()))) for s in samples)


def test_no_rules_means_no_samples(store):
    write(store, "web-1", metric())

    assert store.latest_rule_samples(SINCE) == []


def test_sample_carries_the_rule_and_series_details(store, db_url):
    write(store, "web-1", metric(value=91.5, environment="dev"))
    rule_id = add_rule(db_url, threshold=85.0)

    (s,) = store.latest_rule_samples(SINCE)

    assert (s.rule_id, s.rule_name, s.metric_name, s.operator, s.threshold) == (rule_id, "rule", "cpu.usage", ">", 85.0)
    assert (s.hostname, s.unit, s.tags, s.value, s.timestamp) == ("web-1", "percent", {"environment": "dev"}, 91.5, TS)


def test_fleet_wide_rule_covers_every_host_and_series(store, db_url):
    write(store, "web-1", metric(), metric(core="0"))
    write(store, "web-2", metric())
    rule = add_rule(db_url)

    assert series(store.latest_rule_samples(SINCE)) == [
        (rule, "web-1", ()), (rule, "web-1", (("core", "0"),)), (rule, "web-2", ()),
    ]


def test_per_host_rule_covers_only_that_host(store, db_url):
    write(store, "web-1", metric())
    write(store, "web-2", metric())
    rule = add_rule(db_url, host="web-2")

    assert series(store.latest_rule_samples(SINCE)) == [(rule, "web-2", ())]


def test_null_filter_value_targets_overall_cpu_only(store, db_url):
    write(store, "web-1", metric(environment="dev"), metric(core="0", environment="dev"))
    rule = add_rule(db_url, tag_filter={"core": None})

    assert series(store.latest_rule_samples(SINCE)) == [(rule, "web-1", (("environment", "dev"),))]


def test_string_filter_value_targets_that_series(store, db_url):
    write(store, "web-1", metric(core="0"), metric(core="1"))
    rule = add_rule(db_url, tag_filter={"core": "1"})

    assert series(store.latest_rule_samples(SINCE)) == [(rule, "web-1", (("core", "1"),))]


def test_only_the_latest_sample_per_series_is_returned(store, db_url):
    write(store, "web-1", metric(value=10.0, ts=TS - 10), metric(value=99.0, ts=TS), metric(value=50.0, ts=TS - 5))
    add_rule(db_url)

    (s,) = store.latest_rule_samples(SINCE)

    assert (s.value, s.timestamp) == (99.0, TS)


def test_samples_older_than_since_are_ignored(store, db_url):
    write(store, "web-1", metric(ts=SINCE - 1))
    write(store, "web-2", metric(ts=SINCE))
    add_rule(db_url)

    assert [s.hostname for s in store.latest_rule_samples(SINCE)] == ["web-2"]


def test_disabled_rules_are_skipped(store, db_url):
    write(store, "web-1", metric())
    add_rule(db_url, enabled=False)

    assert store.latest_rule_samples(SINCE) == []


def test_rule_only_sees_its_own_metric(store, db_url):
    write(store, "web-1", metric(), metric(name="memory.used", unit="MB"))
    add_rule(db_url, metric_name="memory.used")

    assert [s.metric_name for s in store.latest_rule_samples(SINCE)] == ["memory.used"]


def test_rule_for_a_metric_nobody_reports_matches_nothing(store, db_url):
    write(store, "web-1", metric())
    add_rule(db_url, metric_name="gpu.usage")

    assert store.latest_rule_samples(SINCE) == []


def test_each_rule_gets_its_own_copy_of_a_shared_series(store, db_url):
    write(store, "web-1", metric())
    first = add_rule(db_url, threshold=80.0)
    second = add_rule(db_url, threshold=90.0)

    assert series(store.latest_rule_samples(SINCE)) == [(first, "web-1", ()), (second, "web-1", ())]
