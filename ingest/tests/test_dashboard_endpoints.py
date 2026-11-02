import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg_pool import PoolTimeout

from pulse_ingest.app import create_app
from pulse_ingest.store import HistoryPoint, HistorySeries, HostSummary, LatestMetric
from tests.fakes import FakeStore


def client_for(hosts=None, error=None, host_summaries=None):
    return TestClient(
        create_app(api_keys={"k"}, store=FakeStore(hosts=hosts, error=error, host_summaries=host_summaries))
    )


# --- GET /hosts ---


def test_hosts_is_empty_when_none_have_reported():
    response = client_for().get("/hosts")

    assert response.status_code == 200
    assert response.json() == {"hosts": []}


def test_hosts_lists_each_host_with_its_key_metrics_and_status():
    summaries = [
        HostSummary("web-1", 100.0, 200.0, status="online", cpu_usage=12.5, memory_percent=50.0),
        HostSummary("web-2", 90.0, 95.0, status="offline", cpu_usage=None, memory_percent=None),
    ]

    response = client_for(host_summaries=summaries).get("/hosts")

    assert response.status_code == 200
    assert response.json() == {
        "hosts": [
            {
                "hostname": "web-1",
                "first_seen_at": 100.0,
                "last_seen_at": 200.0,
                "status": "online",
                "cpu_usage": 12.5,
                "memory_percent": 50.0,
            },
            {
                "hostname": "web-2",
                "first_seen_at": 90.0,
                "last_seen_at": 95.0,
                "status": "offline",
                "cpu_usage": None,
                "memory_percent": None,
            },
        ]
    }


def test_hosts_does_not_require_an_api_key():
    response = client_for().get("/hosts", headers={})

    assert response.status_code == 200


@pytest.mark.parametrize("error", [psycopg.OperationalError("down"), PoolTimeout("timeout")])
def test_hosts_returns_503_when_database_is_unreachable(error):
    response = client_for(error=error).get("/hosts")

    assert response.status_code == 503


# --- GET /hosts/{host} ---


def test_host_summary_returns_404_for_unknown_host():
    response = client_for().get("/hosts/nope")

    assert response.status_code == 404
    assert "nope" in response.json()["detail"]


def test_host_summary_returns_the_matching_host():
    summaries = [
        HostSummary("web-1", 100.0, 200.0, status="online", cpu_usage=12.5, memory_percent=50.0),
        HostSummary("web-2", 90.0, 95.0, status="offline", cpu_usage=None, memory_percent=None),
    ]

    response = client_for(host_summaries=summaries).get("/hosts/web-2")

    assert response.status_code == 200
    assert response.json() == {
        "hostname": "web-2",
        "first_seen_at": 90.0,
        "last_seen_at": 95.0,
        "status": "offline",
        "cpu_usage": None,
        "memory_percent": None,
    }


def test_host_summary_does_not_require_an_api_key():
    summaries = [HostSummary("web-1", 0.0, 0.0, status="online")]

    response = client_for(host_summaries=summaries).get("/hosts/web-1", headers={})

    assert response.status_code == 200


@pytest.mark.parametrize("error", [psycopg.OperationalError("down"), PoolTimeout("timeout")])
def test_host_summary_returns_503_when_database_is_unreachable(error):
    response = client_for(error=error).get("/hosts/web-1")

    assert response.status_code == 503


# --- GET /hosts/{host}/metrics/latest ---


def test_latest_returns_404_for_unknown_host():
    response = client_for().get("/hosts/nope/metrics/latest")

    assert response.status_code == 404
    assert "nope" in response.json()["detail"]


def test_latest_returns_every_series_for_a_known_host():
    hosts = {
        "web-1": {
            "latest": [
                LatestMetric("cpu.usage", "percent", {"core": "0"}, 12.5, 1789419042.0),
                LatestMetric("cpu.usage", "percent", {"core": "1"}, 30.0, 1789419042.0),
                LatestMetric("memory.used", "MB", {}, 512.0, 1789419040.0),
            ]
        }
    }

    response = client_for(hosts).get("/hosts/web-1/metrics/latest")

    assert response.status_code == 200
    body = response.json()
    assert body["host"] == "web-1"
    assert len(body["metrics"]) == 3
    assert body["metrics"][0] == {
        "name": "cpu.usage", "unit": "percent", "tags": {"core": "0"},
        "value": 12.5, "timestamp": 1789419042.0,
    }


def test_latest_is_empty_list_for_a_host_with_no_samples_yet():
    response = client_for({"web-1": {"latest": []}}).get("/hosts/web-1/metrics/latest")

    assert response.status_code == 200
    assert response.json()["metrics"] == []


def test_latest_does_not_require_an_api_key():
    response = client_for({"web-1": {"latest": []}}).get(
        "/hosts/web-1/metrics/latest", headers={}
    )
    assert response.status_code == 200


@pytest.mark.parametrize("error", [psycopg.OperationalError("down"), PoolTimeout("timeout")])
def test_latest_returns_503_when_database_is_unreachable(error):
    response = client_for(error=error).get("/hosts/web-1/metrics/latest")

    assert response.status_code == 503


# --- GET /hosts/{host}/metrics/{name} ---


def test_history_returns_404_for_unknown_host():
    response = client_for().get("/hosts/nope/metrics/cpu.usage")

    assert response.status_code == 404


def test_history_is_empty_series_for_a_metric_never_reported():
    response = client_for({"web-1": {}}).get(
        "/hosts/web-1/metrics/cpu.usage", params={"start": 0, "end": 100}
    )

    assert response.status_code == 200
    assert response.json()["series"] == []


def test_history_groups_points_into_one_series_per_tag_set():
    hosts = {
        "web-1": {
            "history": {
                "cpu.usage": [
                    HistorySeries("percent", {"core": "0"}, [
                        HistoryPoint(100.0, 10.0), HistoryPoint(101.0, 12.0),
                    ]),
                    HistorySeries("percent", {"core": "1"}, [HistoryPoint(100.0, 40.0)]),
                ]
            }
        }
    }

    response = client_for(hosts).get(
        "/hosts/web-1/metrics/cpu.usage", params={"start": 0, "end": 200}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["host"] == "web-1"
    assert body["name"] == "cpu.usage"
    assert len(body["series"]) == 2
    assert body["series"][0]["tags"] == {"core": "0"}
    assert body["series"][0]["points"] == [
        {"timestamp": 100.0, "value": 10.0}, {"timestamp": 101.0, "value": 12.0}
    ]


def test_history_defaults_to_the_last_hour_when_no_range_given():
    response = client_for({"web-1": {}}).get("/hosts/web-1/metrics/cpu.usage")

    assert response.status_code == 200
    body = response.json()
    assert body["end"] - body["start"] == pytest.approx(3600, abs=1)


def test_history_rejects_start_after_end():
    response = client_for({"web-1": {}}).get(
        "/hosts/web-1/metrics/cpu.usage", params={"start": 100, "end": 50}
    )

    assert response.status_code == 400


def test_history_rejects_a_range_over_30_days():
    response = client_for({"web-1": {}}).get(
        "/hosts/web-1/metrics/cpu.usage", params={"start": 0, "end": 31 * 24 * 3600}
    )

    assert response.status_code == 400


def test_history_does_not_require_an_api_key():
    response = client_for({"web-1": {}}).get(
        "/hosts/web-1/metrics/cpu.usage", params={"start": 0, "end": 100}, headers={}
    )
    assert response.status_code == 200


@pytest.mark.parametrize("error", [psycopg.OperationalError("down"), PoolTimeout("timeout")])
def test_history_returns_503_when_database_is_unreachable(error):
    response = client_for(error=error).get("/hosts/web-1/metrics/cpu.usage")

    assert response.status_code == 503


# --- GET /hosts/{host}/metrics/{name}/baseline ---


def baseline_client(history, store_out=None):
    store = FakeStore(hosts={"web-1": {"history": history}})
    if store_out is not None:
        store_out.append(store)
    return TestClient(create_app(api_keys={"k"}, store=store))


def steady(start, count, value=10.0, step=10.0):
    return [HistoryPoint(start + i * step, value) for i in range(count)]


def test_baseline_fetches_one_window_before_start():
    stores = []
    client = baseline_client({}, stores)

    response = client.get(
        "/hosts/web-1/metrics/cpu.usage/baseline", params={"start": 10_000, "end": 20_000, "window": 600}
    )

    assert response.status_code == 200
    assert stores[0].history_queries == [("web-1", "cpu.usage", 9_400, 20_000)]


def test_baseline_returns_each_point_in_range_with_its_mean_and_stddev():
    # 40 steady samples before start, so the first one in range has a full baseline.
    points = steady(9_600, 40) + [HistoryPoint(10_000.0, 55.0)]
    history = {"cpu.usage": [HistorySeries("percent", {"core": "0"}, points)]}

    response = baseline_client(history).get(
        "/hosts/web-1/metrics/cpu.usage/baseline", params={"start": 10_000, "end": 10_100, "window": 600}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["host"] == "web-1"
    assert body["name"] == "cpu.usage"
    assert body["window_seconds"] == 600
    assert body["min_samples"] == 30
    (series,) = body["series"]
    assert series["unit"] == "percent"
    assert series["tags"] == {"core": "0"}
    assert series["points"] == [
        {"timestamp": 10_000.0, "value": 55.0, "samples": 40, "mean": 10.0, "stddev": 0.0}
    ]


def test_baseline_mean_and_stddev_are_null_until_enough_samples():
    history = {"cpu.usage": [HistorySeries("percent", {}, steady(10_000, 3))]}

    response = baseline_client(history).get(
        "/hosts/web-1/metrics/cpu.usage/baseline", params={"start": 10_000, "end": 10_100}
    )

    (series,) = response.json()["series"]
    assert [(p["samples"], p["mean"], p["stddev"]) for p in series["points"]] == [
        (0, None, None), (1, None, None), (2, None, None)
    ]


def test_baseline_leaves_out_a_series_with_no_samples_in_range():
    history = {
        "disk.usage": [
            HistorySeries("percent", {"mount": "/old"}, steady(9_000, 5)),
            HistorySeries("percent", {"mount": "/"}, steady(10_000, 2)),
        ]
    }

    response = baseline_client(history).get(
        "/hosts/web-1/metrics/disk.usage/baseline", params={"start": 10_000, "end": 10_100}
    )

    assert [s["tags"] for s in response.json()["series"]] == [{"mount": "/"}]


def test_baseline_defaults_to_the_last_hour_and_a_one_hour_window():
    response = baseline_client({}).get("/hosts/web-1/metrics/cpu.usage/baseline")

    assert response.status_code == 200
    body = response.json()
    assert body["end"] - body["start"] == pytest.approx(3600, abs=1)
    assert body["window_seconds"] == 3600


@pytest.mark.parametrize("window", [59, 86_401, "abc"])
def test_baseline_rejects_a_window_out_of_bounds(window):
    response = baseline_client({}).get("/hosts/web-1/metrics/cpu.usage/baseline", params={"window": window})

    assert response.status_code == 422


@pytest.mark.parametrize("params", [{"start": 100, "end": 50}, {"start": 0, "end": 31 * 24 * 3600}])
def test_baseline_rejects_the_same_ranges_as_history(params):
    response = baseline_client({}).get("/hosts/web-1/metrics/cpu.usage/baseline", params=params)

    assert response.status_code == 400


def test_baseline_returns_404_for_unknown_host():
    response = client_for().get("/hosts/nope/metrics/cpu.usage/baseline")

    assert response.status_code == 404


def test_baseline_does_not_require_an_api_key():
    response = baseline_client({}).get("/hosts/web-1/metrics/cpu.usage/baseline", headers={})

    assert response.status_code == 200


@pytest.mark.parametrize("error", [psycopg.OperationalError("down"), PoolTimeout("timeout")])
def test_baseline_returns_503_when_database_is_unreachable(error):
    response = client_for(error=error).get("/hosts/web-1/metrics/cpu.usage/baseline")

    assert response.status_code == 503


# --- GET /hosts/{host}/metrics/{name}/anomalies ---


def anomalies_of(history, **params):
    return baseline_client(history).get(
        "/hosts/web-1/metrics/cpu.usage/anomalies", params={"start": 10_000, "end": 10_100, "window": 600, **params}
    )


def test_anomalies_fetches_one_window_before_start():
    stores = []
    client = baseline_client({}, stores)

    client.get("/hosts/web-1/metrics/cpu.usage/anomalies", params={"start": 10_000, "end": 20_000, "window": 600})

    assert stores[0].history_queries == [("web-1", "cpu.usage", 9_400, 20_000)]


def test_anomalies_returns_each_run_with_its_points_and_baselines():
    # A steady 10 +/- 0 before start: spread is floored at 1% of the mean, 0.1.
    points = steady(9_600, 40) + [HistoryPoint(10_000.0, 10.5), HistoryPoint(10_010.0, 11.0), HistoryPoint(10_020.0, 10.0)]
    history = {"cpu.usage": [HistorySeries("percent", {"core": "0"}, points)]}

    response = anomalies_of(history)

    assert response.status_code == 200
    body = response.json()
    assert (body["host"], body["name"], body["window_seconds"], body["min_samples"], body["sigma"]) == (
        "web-1", "cpu.usage", 600, 30, 3.0
    )
    (series,) = body["series"]
    assert (series["unit"], series["tags"]) == ("percent", {"core": "0"})
    (anomaly,) = series["anomalies"]
    assert (anomaly["start"], anomaly["end"], anomaly["direction"]) == (10_000.0, 10_010.0, "above")
    first, second = anomaly["points"]
    assert first == {"timestamp": 10_000.0, "value": 10.5, "mean": 10.0, "spread": pytest.approx(0.1),
                     "deviation": pytest.approx(5.0)}
    assert second["timestamp"] == 10_010.0
    assert second["mean"] == pytest.approx((40 * 10 + 10.5) / 41)


def test_anomalies_leaves_out_series_with_none():
    history = {
        "cpu.usage": [
            HistorySeries("percent", {"core": "0"}, steady(9_600, 45)),
            HistorySeries("percent", {"core": "1"}, steady(9_600, 40) + [HistoryPoint(10_000.0, 90.0)]),
        ]
    }

    response = anomalies_of(history)

    assert [s["tags"] for s in response.json()["series"]] == [{"core": "1"}]


def test_anomalies_uses_the_given_sigma():
    points = steady(9_600, 40) + [HistoryPoint(10_000.0, 10.35)]  # 3.5 sigma
    history = {"cpu.usage": [HistorySeries("percent", {}, points)]}

    assert len(anomalies_of(history, sigma=3).json()["series"]) == 1
    assert anomalies_of(history, sigma=4).json()["series"] == []
    assert anomalies_of(history, sigma=4).json()["sigma"] == 4.0


def test_an_infinite_deviation_is_null_with_its_direction():
    points = steady(9_600, 40, value=0.0) + [HistoryPoint(10_000.0, 3.0)]
    history = {"cpu.usage": [HistorySeries("count", {}, points)]}

    response = anomalies_of(history)

    assert response.status_code == 200
    (anomaly,) = response.json()["series"][0]["anomalies"]
    assert anomaly["direction"] == "above"
    assert anomaly["points"][0]["deviation"] is None


def test_anomalies_defaults_to_the_last_hour_a_one_hour_window_and_three_sigma():
    response = baseline_client({}).get("/hosts/web-1/metrics/cpu.usage/anomalies")

    assert response.status_code == 200
    body = response.json()
    assert body["end"] - body["start"] == pytest.approx(3600, abs=1)
    assert (body["window_seconds"], body["sigma"]) == (3600, 3.0)


@pytest.mark.parametrize("params", [{"sigma": 0}, {"sigma": -2}, {"sigma": 101}, {"sigma": "inf"},
                                    {"sigma": "nan"}, {"window": 59}, {"window": 86_401}])
def test_anomalies_rejects_out_of_bounds_parameters(params):
    response = baseline_client({}).get("/hosts/web-1/metrics/cpu.usage/anomalies", params=params)

    assert response.status_code == 422


@pytest.mark.parametrize("params", [{"start": 100, "end": 50}, {"start": 0, "end": 31 * 24 * 3600}])
def test_anomalies_rejects_the_same_ranges_as_history(params):
    response = baseline_client({}).get("/hosts/web-1/metrics/cpu.usage/anomalies", params=params)

    assert response.status_code == 400


def test_anomalies_returns_404_for_unknown_host():
    assert client_for().get("/hosts/nope/metrics/cpu.usage/anomalies").status_code == 404


def test_anomalies_does_not_require_an_api_key():
    assert baseline_client({}).get("/hosts/web-1/metrics/cpu.usage/anomalies", headers={}).status_code == 200


@pytest.mark.parametrize("error", [psycopg.OperationalError("down"), PoolTimeout("timeout")])
def test_anomalies_returns_503_when_database_is_unreachable(error):
    assert client_for(error=error).get("/hosts/web-1/metrics/cpu.usage/anomalies").status_code == 503
