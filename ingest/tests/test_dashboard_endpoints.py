import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg_pool import PoolTimeout

from pulse_ingest.app import create_app
from pulse_ingest.store import LatestMetric
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
