import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg_pool import PoolTimeout

from pulse_ingest.app import create_app
from tests.fakes import FakeStore


def build_client(store):
    return TestClient(
        create_app(api_keys={"test-key"}, store=store),
        headers={"Authorization": "Bearer test-key"},
    )


@pytest.fixture
def client():
    return build_client(FakeStore())


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
    assert response.json() == {"accepted": 2, "stored": 2, "new_host": True}


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


def test_valid_batch_is_handed_to_the_store():
    store = FakeStore()
    client = build_client(store)

    client.post("/metrics", json={"host": "web-1", "metrics": [metric()]})

    assert len(store.batches) == 1
    assert store.batches[0].host == "web-1"
    assert store.batches[0].metrics[0].name == "cpu.usage"


def test_invalid_batch_never_reaches_the_store():
    store = FakeStore()
    client = build_client(store)

    client.post("/metrics", json={"host": "web-1", "metrics": []})

    assert store.batches == []


@pytest.mark.parametrize("error", [psycopg.OperationalError("db down"), PoolTimeout("no connections")])
def test_database_outage_is_503_so_agents_retry(error):
    client = build_client(FakeStore(error=error))

    response = client.post("/metrics", json={"host": "web-1", "metrics": [metric()]})

    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable"}


def test_first_batch_from_a_host_reports_new_host_true():
    client = build_client(FakeStore())

    response = client.post("/metrics", json={"host": "new-host", "metrics": [metric()]})

    assert response.json()["new_host"] is True


def test_second_batch_from_the_same_host_reports_new_host_false():
    client = build_client(FakeStore())
    body = {"host": "web-1", "metrics": [metric()]}

    client.post("/metrics", json=body)
    response = client.post("/metrics", json=body)

    assert response.json()["new_host"] is False


def test_different_hosts_are_each_new_once():
    client = build_client(FakeStore())

    first = client.post("/metrics", json={"host": "web-1", "metrics": [metric()]})
    second = client.post("/metrics", json={"host": "web-2", "metrics": [metric()]})

    assert first.json()["new_host"] is True
    assert second.json()["new_host"] is True


def test_new_host_registration_is_actually_logged(caplog):
    # A regression guard: logger.info() calls are silently dropped unless
    # the root logger has a handler configured, which is easy to get wrong
    # for a plain `uvicorn module:app` entrypoint with no __main__ of its own.
    client = build_client(FakeStore())

    with caplog.at_level("INFO"):
        client.post("/metrics", json={"host": "brand-new", "metrics": [metric()]})

    assert "brand-new" in caplog.text
    assert "registered" in caplog.text.lower()


def test_repeat_host_is_not_logged_as_newly_registered(caplog):
    client = build_client(FakeStore())
    body = {"host": "web-1", "metrics": [metric()]}
    client.post("/metrics", json=body)

    with caplog.at_level("INFO"):
        caplog.clear()
        client.post("/metrics", json=body)

    assert "registered" not in caplog.text.lower()
