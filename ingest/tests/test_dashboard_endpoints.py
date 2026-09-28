import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg_pool import PoolTimeout

from pulse_ingest.app import create_app
from pulse_ingest.store import HistoryPoint, HistorySeries, LatestMetric
from tests.fakes import FakeStore


def client_for(hosts=None, error=None):
    return TestClient(create_app(api_keys={"k"}, store=FakeStore(hosts=hosts, error=error)))


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
