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


def test_list_hosts_is_empty_initially(store):
    assert store.list_hosts() == []


def test_list_hosts_returns_every_known_host(store):
    store.write_batch(batch_dict("web-1", metric()))
    store.write_batch(batch_dict("web-2", metric()))

    hostnames = {h.hostname for h in store.list_hosts()}

    assert hostnames == {"web-1", "web-2"}


def test_list_hosts_reports_first_and_last_seen(store):
    store.write_batch(batch_dict("web-1", metric()))

    (host,) = store.list_hosts()

    assert host.hostname == "web-1"
    assert host.first_seen_at == pytest.approx(host.last_seen_at, abs=1.0)


def test_list_hosts_includes_key_metrics_when_present(store):
    store.write_batch(batch_dict(
        "web-1",
        metric(name="cpu.usage", value=42.0),
        metric(name="memory.used", unit="MB", value=512.0),
        metric(name="memory.total", unit="MB", value=2048.0),
    ))

    (host,) = store.list_hosts()

    assert host.cpu_usage == 42.0
    assert host.memory_percent == pytest.approx(25.0)


def test_list_hosts_key_metrics_are_none_for_a_host_with_no_data_yet(store):
    store.write_batch(batch_dict("web-1", metric(name="load.avg")))  # no cpu.usage/memory.*

    (host,) = store.list_hosts()

    assert host.cpu_usage is None
    assert host.memory_percent is None


def test_list_hosts_ignores_tagged_readings_for_key_metrics(store):
    # A per-core cpu.usage reading should not be mistaken for the overall one.
    store.write_batch(batch_dict("web-1", metric(name="cpu.usage", core="0", value=99.0)))

    (host,) = store.list_hosts()

    assert host.cpu_usage is None


def test_list_hosts_key_metrics_use_the_latest_sample(store):
    store.write_batch(batch_dict("web-1", metric(name="cpu.usage", value=10.0, ts=TS)))
    store.write_batch(batch_dict("web-1", metric(name="cpu.usage", value=20.0, ts=TS + 10)))

    (host,) = store.list_hosts()

    assert host.cpu_usage == 20.0


def test_list_hosts_key_metrics_are_independent_per_host(store):
    store.write_batch(batch_dict("web-1", metric(name="cpu.usage", value=10.0)))
    store.write_batch(batch_dict("web-2", metric(name="cpu.usage", value=90.0)))

    by_host = {h.hostname: h.cpu_usage for h in store.list_hosts()}

    assert by_host == {"web-1": 10.0, "web-2": 90.0}


def test_list_hosts_orders_most_recently_active_first(store):
    store.write_batch(batch_dict("web-1", metric()))
    store.write_batch(batch_dict("web-2", metric()))
    store.write_batch(batch_dict("web-1", metric(ts=TS + 10)))  # web-1 reports again, latest now

    hostnames = [h.hostname for h in store.list_hosts()]

    assert hostnames == ["web-1", "web-2"]


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


def test_get_history_returns_points_within_range_only(store):
    store.write_batch(batch_dict(
        "web-1",
        metric(value=1.0, ts=TS),
        metric(value=2.0, ts=TS + 100),
        metric(value=3.0, ts=TS + 200),
    ))

    (series,) = store.get_history("web-1", "cpu.usage", TS + 50, TS + 150)

    assert [p.value for p in series.points] == [2.0]


def test_get_history_range_is_inclusive_on_both_ends(store):
    store.write_batch(batch_dict("web-1", metric(value=1.0, ts=TS), metric(value=2.0, ts=TS + 10)))

    (series,) = store.get_history("web-1", "cpu.usage", TS, TS + 10)

    assert [p.value for p in series.points] == [1.0, 2.0]


def test_get_history_orders_points_by_time(store):
    store.write_batch(batch_dict(
        "web-1", metric(value=3.0, ts=TS + 20), metric(value=1.0, ts=TS), metric(value=2.0, ts=TS + 10)
    ))

    (series,) = store.get_history("web-1", "cpu.usage", TS, TS + 20)

    assert [p.value for p in series.points] == [1.0, 2.0, 3.0]


def test_get_history_splits_series_by_tags(store):
    store.write_batch(batch_dict(
        "web-1",
        metric(core="0", value=10.0, ts=TS),
        metric(core="1", value=20.0, ts=TS),
        metric(core="0", value=15.0, ts=TS + 10),
    ))

    series = {s.tags["core"]: s for s in store.get_history("web-1", "cpu.usage", TS, TS + 10)}

    assert [p.value for p in series["0"].points] == [10.0, 15.0]
    assert [p.value for p in series["1"].points] == [20.0]


def test_get_history_is_empty_for_a_metric_the_host_never_reported(store):
    store.write_batch(batch_dict("web-1", metric(name="cpu.usage")))

    assert store.get_history("web-1", "memory.used", TS - 100, TS + 100) == []


def test_get_history_does_not_mix_different_hosts(store):
    store.write_batch(batch_dict("web-1", metric(value=1.0, ts=TS)))
    store.write_batch(batch_dict("web-2", metric(value=2.0, ts=TS)))

    (series,) = store.get_history("web-1", "cpu.usage", TS, TS)

    assert [p.value for p in series.points] == [1.0]


def test_get_history_excludes_samples_outside_the_range(store):
    store.write_batch(batch_dict("web-1", metric(value=1.0, ts=TS - 1000), metric(value=2.0, ts=TS + 1000)))

    assert store.get_history("web-1", "cpu.usage", TS - 1, TS + 1) == []
