import pytest

from pulse_ingest.migrate import apply_migrations
from pulse_ingest.schemas import MetricBatch
from pulse_ingest.store import PostgresMetricStore

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


def metric(name="cpu.usage", unit="percent", value=1.0, ts=TS, **tags):
    return {"name": name, "unit": unit, "value": value, "timestamp": ts, "tags": tags}


def batch_dict(host, *metrics):
    return MetricBatch(host=host, metrics=list(metrics))


def test_host_exists_is_false_before_any_data(store):
    assert store.host_exists("web-1") is False


def test_host_exists_becomes_true_after_a_write(store):
    store.write_batch(batch_dict("web-1", metric()))

    assert store.host_exists("web-1") is True
    assert store.host_exists("web-2") is False


def test_get_latest_returns_one_row_per_series(store):
    store.write_batch(batch_dict(
        "web-1",
        metric(name="cpu.usage", core="0", value=10.0, ts=TS),
        metric(name="cpu.usage", core="1", value=20.0, ts=TS),
        metric(name="memory.used", unit="MB", value=512.0, ts=TS),
    ))

    latest = {(m.name, m.tags.get("core")): m for m in store.get_latest("web-1")}

    assert len(latest) == 3
    assert latest[("cpu.usage", "0")].value == 10.0
    assert latest[("cpu.usage", "1")].value == 20.0
    assert latest[("memory.used", None)].value == 512.0
    assert latest[("memory.used", None)].unit == "MB"


def test_get_latest_picks_the_newest_sample_per_series(store):
    store.write_batch(batch_dict("web-1", metric(value=10.0, ts=TS)))
    store.write_batch(batch_dict("web-1", metric(value=20.0, ts=TS + 10)))

    (latest,) = store.get_latest("web-1")

    assert latest.value == 20.0
    assert latest.timestamp == pytest.approx(TS + 10)


def test_get_latest_is_empty_for_a_host_with_no_samples(store):
    assert store.get_latest("web-1") == []


def test_get_latest_does_not_mix_series_from_different_hosts(store):
    store.write_batch(batch_dict("web-1", metric(value=1.0)))
    store.write_batch(batch_dict("web-2", metric(value=2.0)))

    (a,) = store.get_latest("web-1")
    (b,) = store.get_latest("web-2")
    assert (a.value, b.value) == (1.0, 2.0)
