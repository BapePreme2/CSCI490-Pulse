import math
import random
import statistics

import pytest
from fastapi.testclient import TestClient

from pulse_ingest.anomaly import MIN_BASELINE_SAMPLES, baseline, deviation, rolling_baseline, spread
from pulse_ingest.app import create_app
from pulse_ingest.migrate import apply_migrations
from pulse_ingest.schemas import MetricBatch
from pulse_ingest.store import HistoryPoint, PostgresMetricStore


def series(*values, start=0.0, step=10.0):
    return [HistoryPoint(start + i * step, float(v)) for i, v in enumerate(values)]


def test_no_baseline_until_the_window_holds_the_minimum_samples():
    points = series(*range(5))

    result = rolling_baseline(points, window_seconds=3600, min_samples=3)

    assert [p.samples for p in result] == [0, 1, 2, 3, 4]
    assert [p.mean for p in result] == [None, None, None, 1.0, 1.5]
    assert result[2].stddev is None
    assert result[3].stddev == pytest.approx(1.0)


def test_baseline_is_the_mean_and_sample_stddev_of_the_samples_before():
    values = [2, 4, 4, 4, 5, 5, 7, 9]
    points = series(*values, 100)

    last = rolling_baseline(points, window_seconds=3600, min_samples=2)[-1]

    assert last.samples == len(values)
    assert last.mean == pytest.approx(statistics.fmean(values))
    assert last.stddev == pytest.approx(statistics.stdev(values))


def test_a_sample_is_left_out_of_its_own_baseline():
    points = series(*[10] * 10, 1000)

    spike = rolling_baseline(points, window_seconds=3600, min_samples=2)[-1]

    assert spike.value == 1000
    assert spike.mean == 10
    assert spike.stddev == 0


def test_the_spike_is_part_of_the_baselines_after_it():
    points = series(*[10] * 10, 1000, 10)

    after = rolling_baseline(points, window_seconds=3600, min_samples=2)[-1]

    assert after.mean == pytest.approx((10 * 10 + 1000) / 11)


def test_samples_older_than_the_window_drop_out():
    # 10s apart with a 30s window: each baseline covers [t - 30, t), so the
    # three samples before it.
    points = series(1, 2, 3, 100, 200, 300, 0)

    result = rolling_baseline(points, window_seconds=30, min_samples=2)

    assert [p.samples for p in result] == [0, 1, 2, 3, 3, 3, 3]
    assert result[-1].mean == pytest.approx(200)
    assert result[3].mean == pytest.approx(2)


def test_a_sample_exactly_one_window_old_is_still_in_it():
    points = [HistoryPoint(0.0, 1.0), HistoryPoint(10.0, 3.0), HistoryPoint(30.0, 0.0)]

    last = rolling_baseline(points, window_seconds=30, min_samples=2)[-1]

    assert last.samples == 2
    assert last.mean == 2


def test_a_gap_longer_than_the_window_starts_the_baseline_over():
    before = series(*[50] * 40)
    after = series(*[1, 2, 3], start=10_000)

    result = rolling_baseline(before + after, window_seconds=60, min_samples=2)

    assert [p.samples for p in result[-3:]] == [0, 1, 2]
    assert result[-1].mean == pytest.approx(1.5)


def test_a_constant_series_has_exactly_zero_stddev():
    points = series(*[16384.0] * 100)

    result = rolling_baseline(points, window_seconds=300, min_samples=2)

    assert {p.stddev for p in result if p.stddev is not None} == {0.0}


def test_stddev_settles_near_zero_once_a_step_change_leaves_the_window():
    points = series(*[10.0] * 50, *[73.3] * 50)

    last = rolling_baseline(points, window_seconds=300, min_samples=2)[-1]

    assert last.mean == pytest.approx(73.3)
    assert last.stddev == pytest.approx(0.0, abs=1e-6)


@pytest.mark.parametrize("offset", [0.0, 1e9])
def test_matches_computing_each_window_from_scratch(offset):
    # A large offset is where a running sum of squares would cancel badly.
    rng = random.Random(490)
    points = []
    ts = 0.0
    for _ in range(2000):
        ts += rng.choice([10.0, 10.0, 10.0, 11.5, 45.0])  # mostly steady, some gaps
        points.append(HistoryPoint(ts, offset + rng.gauss(50, 15)))

    result = rolling_baseline(points, window_seconds=600, min_samples=5)

    for i, got in enumerate(result):
        t = points[i].timestamp
        window = [p.value for p in points[:i] if p.timestamp >= t - 600]
        assert got.samples == len(window)
        if len(window) < 5:
            assert got.mean is None and got.stddev is None
        else:
            assert got.mean == pytest.approx(statistics.fmean(window), rel=1e-12)
            assert got.stddev == pytest.approx(statistics.stdev(window), rel=1e-6)


def test_samples_before_start_feed_the_baseline_but_are_not_returned():
    points = series(*range(10))

    result = rolling_baseline(points, window_seconds=3600, min_samples=2, start=70)

    assert [p.timestamp for p in result] == [70, 80, 90]
    assert result[0].samples == 7
    assert result[0].mean == pytest.approx(3)


def test_empty_series_has_no_baseline():
    assert rolling_baseline([], window_seconds=60) == []


def test_default_minimum_is_five_minutes_of_10s_samples():
    points = series(*[1] * 31)

    result = rolling_baseline(points, window_seconds=3600)

    assert MIN_BASELINE_SAMPLES == 30
    assert result[29].mean is None
    assert result[30].mean == 1


@pytest.mark.parametrize("window", [0, -5])
def test_rejects_a_window_that_isnt_positive(window):
    with pytest.raises(ValueError):
        rolling_baseline(series(1, 2), window_seconds=window)


def test_rejects_a_minimum_below_two_samples():
    with pytest.raises(ValueError):
        rolling_baseline(series(1, 2), window_seconds=60, min_samples=1)


@pytest.mark.integration
def test_baseline_endpoint_against_postgres(scratch_db_url):
    """Stored samples come back from get_history in the order the rolling
    window needs, one baseline per series even when cores interleave."""
    apply_migrations(scratch_db_url)
    store = PostgresMetricStore(scratch_db_url, min_size=1, max_size=2)
    store.open()
    try:
        rng = random.Random(7)
        expected = {"0": [], "1": []}
        metrics = []
        for i in range(120):
            for core in expected:
                value = (20.0 if core == "0" else 70.0) + rng.uniform(-5, 5)
                expected[core].append(HistoryPoint(1_000_000.0 + i * 10, value))
                metrics.append(
                    {"name": "cpu.usage", "unit": "percent", "value": value,
                     "timestamp": 1_000_000.0 + i * 10, "tags": {"core": core}}
                )
        # Newest first, so storage order can't be what puts them in order.
        store.write_batch(MetricBatch(host="web-1", metrics=metrics[::-1]))

        response = TestClient(create_app(api_keys={"k"}, store=store)).get(
            "/hosts/web-1/metrics/cpu.usage/baseline",
            params={"start": 1_000_600, "end": 1_001_190, "window": 300},
        )
    finally:
        store.close()

    assert response.status_code == 200
    body = response.json()
    assert [s["tags"] for s in body["series"]] == [{"core": "0"}, {"core": "1"}]
    for s in body["series"]:
        want = rolling_baseline(expected[s["tags"]["core"]], window_seconds=300, start=1_000_600)
        assert [p["timestamp"] for p in s["points"]] == [p.timestamp for p in want]
        assert [p["samples"] for p in s["points"]] == [30] * len(want)
        assert [p["mean"] for p in s["points"]] == pytest.approx([p.mean for p in want])
        assert [p["stddev"] for p in s["points"]] == pytest.approx([p.stddev for p in want])


# --- baseline / spread / deviation (used by anomaly rules, ANOM-02) ---


def test_baseline_of_a_set_of_values():
    values = [2, 4, 4, 4, 5, 5, 7, 9]

    b = baseline(values, min_samples=2)

    assert (b.samples, b.mean) == (8, pytest.approx(5.0))
    assert b.stddev == pytest.approx(statistics.stdev(values))


def test_baseline_is_none_below_the_minimum():
    assert baseline([1.0] * 29) is None
    assert baseline([1.0] * 30) is not None


def test_deviation_is_in_standard_deviations_signed_by_direction():
    assert deviation(56.0, 50.0, 2.0) == pytest.approx(3.0)
    assert deviation(44.0, 50.0, 2.0) == pytest.approx(-3.0)


def test_spread_is_floored_at_one_percent_of_the_mean():
    assert spread(50.0, 0.0) == pytest.approx(0.5)
    assert spread(-50.0, 0.1) == pytest.approx(0.5)
    assert spread(50.0, 2.0) == 2.0
    assert deviation(52.0, 50.0, 0.0) == pytest.approx(4.0)


def test_deviation_from_a_constant_zero_baseline():
    assert deviation(0.0, 0.0, 0.0) == 0.0
    assert deviation(0.5, 0.0, 0.0) == math.inf
    assert deviation(-0.5, 0.0, 0.0) == -math.inf
