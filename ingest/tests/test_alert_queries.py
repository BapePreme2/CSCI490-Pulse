import psycopg
import pytest
from psycopg.types.json import Jsonb

from pulse_ingest.migrate import apply_migrations
from pulse_ingest.schemas import MetricBatch
from pulse_ingest.store import PostgresMetricStore

pytestmark = pytest.mark.integration

TS = 1789419042.0
SINCE = TS - 30
PADDING = 30


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


def add_rule(
    db_url, metric_name="cpu.usage", host=None, tag_filter=None, enabled=True, threshold=80.0, duration=0
):
    with psycopg.connect(db_url) as conn:
        return conn.execute(
            "INSERT INTO alert_rules (name, metric_name, operator, threshold, duration_seconds, "
            "host_id, tag_filter, enabled) "
            "VALUES ('rule', %s, '>', %s, %s, (SELECT id FROM hosts WHERE hostname = %s), %s, %s) "
            "RETURNING id",
            (metric_name, threshold, duration, host, Jsonb(tag_filter or {}), enabled),
        ).fetchone()[0]


def timestamps(s):
    return [p.timestamp - TS for p in s.points]


def series(samples):
    return sorted((s.rule_id, s.hostname, tuple(sorted(s.tags.items()))) for s in samples)


def test_no_rules_means_no_samples(store):
    write(store, "web-1", metric())

    assert store.rule_series(SINCE, PADDING) == []


def test_series_carries_the_rule_and_series_details(store, db_url):
    write(store, "web-1", metric(value=91.5, environment="dev"))
    rule_id = add_rule(db_url, threshold=85.0, duration=120)

    (s,) = store.rule_series(SINCE, PADDING)

    assert (s.rule_id, s.rule_name, s.metric_name, s.operator, s.threshold, s.duration_seconds) == (
        rule_id, "rule", "cpu.usage", ">", 85.0, 120
    )
    assert (s.hostname, s.unit, s.tags) == ("web-1", "percent", {"environment": "dev"})
    with psycopg.connect(db_url) as conn:
        assert s.host_id == conn.execute("SELECT id FROM hosts WHERE hostname = 'web-1'").fetchone()[0]
    assert [(p.timestamp, p.value) for p in s.points] == [(TS, 91.5)]


def test_fleet_wide_rule_covers_every_host_and_series(store, db_url):
    write(store, "web-1", metric(), metric(core="0"))
    write(store, "web-2", metric())
    rule = add_rule(db_url)

    assert series(store.rule_series(SINCE, PADDING)) == [
        (rule, "web-1", ()), (rule, "web-1", (("core", "0"),)), (rule, "web-2", ()),
    ]


def test_per_host_rule_covers_only_that_host(store, db_url):
    write(store, "web-1", metric())
    write(store, "web-2", metric())
    rule = add_rule(db_url, host="web-2")

    assert series(store.rule_series(SINCE, PADDING)) == [(rule, "web-2", ())]


def test_null_filter_value_targets_overall_cpu_only(store, db_url):
    write(store, "web-1", metric(environment="dev"), metric(core="0", environment="dev"))
    rule = add_rule(db_url, tag_filter={"core": None})

    assert series(store.rule_series(SINCE, PADDING)) == [(rule, "web-1", (("environment", "dev"),))]


def test_string_filter_value_targets_that_series(store, db_url):
    write(store, "web-1", metric(core="0"), metric(core="1"))
    rule = add_rule(db_url, tag_filter={"core": "1"})

    assert series(store.rule_series(SINCE, PADDING)) == [(rule, "web-1", (("core", "1"),))]


def test_window_is_duration_plus_padding_back_from_the_latest_sample_oldest_first(store, db_url):
    write(store, "web-1", *(metric(ts=TS + offset) for offset in (0, -300, -10, -331, -330, -400)))
    add_rule(db_url, duration=300)

    (s,) = store.rule_series(SINCE, PADDING)

    assert timestamps(s) == [-330, -300, -10, 0]


def test_zero_duration_window_is_just_the_padding(store, db_url):
    write(store, "web-1", *(metric(ts=TS + offset) for offset in (0, -10, -30, -31)))
    add_rule(db_url, duration=0)

    (s,) = store.rule_series(SINCE, PADDING)

    assert timestamps(s) == [-30, -10, 0]


def test_window_follows_each_series_own_latest_sample(store, db_url):
    # Windows are 10s + 30s padding = 40s. web-2 last reported 20s before
    # web-1, so its window is [-60, -20], not [-40, 0].
    write(store, "web-1", metric(ts=TS), metric(ts=TS - 41))
    write(store, "web-2", metric(ts=TS - 20), metric(ts=TS - 55), metric(ts=TS - 61))
    add_rule(db_url, duration=10)

    by_host = {s.hostname: timestamps(s) for s in store.rule_series(SINCE, PADDING)}

    assert by_host == {"web-1": [0], "web-2": [-55, -20]}


def test_each_rule_gets_a_window_for_its_own_duration(store, db_url):
    write(store, "web-1", *(metric(ts=TS - 10 * i) for i in range(20)))
    short = add_rule(db_url, duration=0)
    long = add_rule(db_url, duration=100)

    windows = {s.rule_id: timestamps(s) for s in store.rule_series(SINCE, PADDING)}

    assert windows[short] == [-30, -20, -10, 0]
    assert windows[long] == [-130, -120, -110, -100, -90, -80, -70, -60, -50, -40, -30, -20, -10, 0]


def test_series_do_not_share_points(store, db_url):
    write(store, "web-1", metric(value=1.0, core="0"), metric(value=2.0, core="1"), metric(value=3.0, ts=TS - 10, core="0"))
    add_rule(db_url)

    values = {s.tags["core"]: [p.value for p in s.points] for s in store.rule_series(SINCE, PADDING)}

    assert values == {"0": [3.0, 1.0], "1": [2.0]}


def test_series_whose_latest_sample_is_older_than_since_are_ignored(store, db_url):
    # Even with a long duration, whose window would reach back past SINCE.
    write(store, "web-1", metric(ts=SINCE - 1))
    write(store, "web-2", metric(ts=SINCE))
    add_rule(db_url, duration=3600)

    assert [s.hostname for s in store.rule_series(SINCE, PADDING)] == ["web-2"]


def test_disabled_rules_are_skipped(store, db_url):
    write(store, "web-1", metric())
    add_rule(db_url, enabled=False)

    assert store.rule_series(SINCE, PADDING) == []


def test_rule_only_sees_its_own_metric(store, db_url):
    write(store, "web-1", metric(), metric(name="memory.used", unit="MB"))
    add_rule(db_url, metric_name="memory.used")

    assert [s.metric_name for s in store.rule_series(SINCE, PADDING)] == ["memory.used"]


def test_rule_for_a_metric_nobody_reports_matches_nothing(store, db_url):
    write(store, "web-1", metric())
    add_rule(db_url, metric_name="gpu.usage")

    assert store.rule_series(SINCE, PADDING) == []


def test_each_rule_gets_its_own_copy_of_a_shared_series(store, db_url):
    write(store, "web-1", metric())
    first = add_rule(db_url, threshold=80.0)
    second = add_rule(db_url, threshold=90.0)

    assert series(store.rule_series(SINCE, PADDING)) == [(first, "web-1", ()), (second, "web-1", ())]
