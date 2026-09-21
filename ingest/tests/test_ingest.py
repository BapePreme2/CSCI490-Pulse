import pytest
from fastapi.testclient import TestClient

from pulse_ingest.app import create_app


@pytest.fixture
def client():
    return TestClient(create_app())


def metric(**overrides):
    data = {
        "name": "cpu.usage",
        "value": 42.5,
        "unit": "percent",
        "tags": {"core": "0"},
        "timestamp": 1789419042.579,
    }
    data.update(overrides)
    return data


def test_valid_batch_is_accepted(client):
    body = {"host": "web-1", "metrics": [metric(), metric(name="load.avg", unit="load")]}

    response = client.post("/metrics", json=body)

    assert response.status_code == 202
    assert response.json() == {"accepted": 2}


def test_missing_host_is_rejected_with_field_location(client):
    response = client.post("/metrics", json={"metrics": [metric()]})

    assert response.status_code == 422
    assert ["body", "host"] in [err["loc"] for err in response.json()["detail"]]


def test_invalid_metric_reports_which_one_is_bad(client):
    body = {"host": "web-1", "metrics": [metric(), metric(value="abc")]}

    response = client.post("/metrics", json=body)

    assert response.status_code == 422
    assert ["body", "metrics", 1, "value"] in [err["loc"] for err in response.json()["detail"]]


@pytest.mark.parametrize(
    "body",
    [
        {"host": "web-1", "metrics": []},
        {"host": "web-1", "metrics": [metric()] * 5001},
        {"host": "web-1", "metrics": [metric(unknown_field=1)]},
        {"host": "web-1", "metrics": [metric(timestamp=0)]},
        {"host": "web-1", "metrics": [metric(tags={"core": 0})]},
        {"host": "web-1", "metrics": [metric()], "extra": "nope"},
        ["not", "an", "object"],
    ],
)
def test_bad_bodies_are_rejected(client, body):
    assert client.post("/metrics", json=body).status_code == 422


def test_malformed_json_is_rejected(client):
    response = client.post(
        "/metrics", content="{not json", headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 422


def test_get_is_not_allowed(client):
    assert client.get("/metrics").status_code == 405
