import psycopg
import pytest
from psycopg_pool import PoolTimeout

from pulse_ingest.migrate import apply_migrations
from pulse_ingest.schemas import MetricBatch, MetricIn
from pulse_ingest.store import PostgresMetricStore

pytestmark = pytest.mark.integration

TS = 1789419042.579


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


def metric(name="cpu.usage", unit="percent", value=1.0, ts=TS, **tags):
    return {"name": name, "unit": unit, "value": value, "timestamp": ts, "tags": tags}


def batch(*metrics, host="web-1"):
    return MetricBatch(host=host, metrics=list(metrics))


def test_write_creates_host_metrics_and_samples(store, db_url):
    result = store.write_batch(
        batch(metric(core="0"), metric(core="1"), metric(name="load.avg", unit="load"))
    )

    assert result.stored == 3
    assert query(db_url, "SELECT hostname FROM hosts") == [("web-1",)]
    assert query(db_url, "SELECT name, unit FROM metrics ORDER BY name") == [
        ("cpu.usage", "percent"),
        ("load.avg", "load"),
    ]
    assert query(db_url, "SELECT count(*) FROM metric_values") == [(3,)]


def test_samples_are_linked_to_the_right_host_and_metric(store, db_url):
    store.write_batch(batch(metric(value=42.5, core="0")))

    rows = query(
        db_url,
        "SELECT h.hostname, m.name, m.unit, v.value, v.tags "
        "FROM metric_values v JOIN hosts h ON h.id = v.host_id "
        "JOIN metrics m ON m.id = v.metric_id",
    )
    assert rows == [("web-1", "cpu.usage", "percent", 42.5, {"core": "0"})]


def test_timestamp_round_trips(store, db_url):
    store.write_batch(batch(metric(ts=TS)))

    (epoch,) = query(db_url, "SELECT extract(epoch FROM ts) FROM metric_values")[0]
    assert float(epoch) == pytest.approx(TS, abs=1e-3)


def test_resending_a_batch_is_idempotent(store, db_url):
    b = batch(metric(core="0"), metric(core="1"))

    first = store.write_batch(b)
    again = store.write_batch(b)

    assert (first.stored, again.stored) == (2, 0)
    assert query(db_url, "SELECT count(*) FROM metric_values") == [(2,)]


def test_duplicate_samples_within_one_batch_are_stored_once(store, db_url):
    result = store.write_batch(batch(metric(), metric()))

    assert result.stored == 1
    assert query(db_url, "SELECT count(*) FROM metric_values") == [(1,)]


def test_tags_distinguish_series_at_the_same_timestamp(store, db_url):
    store.write_batch(batch(metric(core="0"), metric(core="1")))

    assert query(db_url, "SELECT count(*) FROM metric_values") == [(2,)]


def test_hosts_and_metrics_are_reused_across_batches(store, db_url):
    store.write_batch(batch(metric(ts=TS)))
    store.write_batch(batch(metric(ts=TS + 10)))

    assert query(db_url, "SELECT count(*) FROM hosts") == [(1,)]
    assert query(db_url, "SELECT count(*) FROM metrics") == [(1,)]
    assert query(db_url, "SELECT count(*) FROM metric_values") == [(2,)]


def test_same_metric_name_with_different_unit_is_a_different_metric(store, db_url):
    store.write_batch(batch(metric(unit="percent"), metric(unit="ratio")))

    assert query(db_url, "SELECT count(*) FROM metrics") == [(2,)]


def test_separate_hosts_get_separate_rows(store, db_url):
    store.write_batch(batch(metric(), host="web-1"))
    store.write_batch(batch(metric(), host="web-2"))

    assert query(db_url, "SELECT hostname FROM hosts ORDER BY hostname") == [("web-1",), ("web-2",)]
    assert query(db_url, "SELECT count(*) FROM metric_values") == [(2,)]


def test_last_seen_advances_on_each_write(store, db_url):
    store.write_batch(batch(metric(ts=TS)))
    (first,) = query(db_url, "SELECT last_seen_at FROM hosts")[0]

    store.write_batch(batch(metric(ts=TS + 10)))
    (second,) = query(db_url, "SELECT last_seen_at FROM hosts")[0]

    assert second > first


def test_agent_host_tag_is_not_stored_on_every_row(store, db_url):
    store.write_batch(batch(metric(host="web-1", environment="dev", core="0")))

    assert query(db_url, "SELECT tags FROM metric_values") == [({"environment": "dev", "core": "0"},)]


def test_failed_write_rolls_back_everything(store, db_url):
    poisoned = MetricIn.model_construct(
        name="bad\x00name", value=1.0, unit="percent", tags={}, timestamp=TS
    )
    b = MetricBatch.model_construct(host="web-1", metrics=[MetricIn(**metric()), poisoned])

    with pytest.raises(psycopg.Error):
        store.write_batch(b)

    assert query(db_url, "SELECT count(*) FROM hosts") == [(0,)]
    assert query(db_url, "SELECT count(*) FROM metrics") == [(0,)]
    assert query(db_url, "SELECT count(*) FROM metric_values") == [(0,)]


def test_maximum_size_batch(store, db_url):
    metrics = [metric(ts=TS + i) for i in range(5000)]

    result = store.write_batch(batch(*metrics))

    assert result.stored == 5000
    assert query(db_url, "SELECT count(*) FROM metric_values") == [(5000,)]


def test_unreachable_database_raises_an_error_the_api_maps_to_503():
    dead = PostgresMetricStore("postgresql://pulse:pulse@localhost:1/none", connect_timeout=0.5)
    dead.open()
    try:
        with pytest.raises((psycopg.OperationalError, PoolTimeout)):
            dead.write_batch(batch(metric()))
    finally:
        dead.close()
