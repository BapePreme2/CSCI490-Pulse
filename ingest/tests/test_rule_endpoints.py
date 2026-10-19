import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg_pool import PoolTimeout

from pulse_ingest.app import create_app
from tests.fakes import FakeStore

ORIGIN = "http://localhost:5173"


def client_for(error=None, known_hosts=None):
    store = FakeStore(error=error, known_hosts=known_hosts or {"web-1"})
    return TestClient(create_app(api_keys={"k"}, store=store, cors_origins=[ORIGIN]))


def rule_body(**overrides):
    body = {
        "name": "High CPU",
        "metric_name": "cpu.usage",
        "operator": ">",
        "threshold": 80,
        "duration_seconds": 300,
        "tag_filter": {"core": None},
    }
    body.update(overrides)
    return body


def test_rules_list_is_empty_initially():
    response = client_for().get("/rules")

    assert response.status_code == 200
    assert response.json() == {"rules": []}


def test_create_returns_the_stored_rule_with_defaults_filled_in():
    response = client_for().post("/rules", json=rule_body())

    assert response.status_code == 201
    assert response.json() == {
        "id": 1,
        "name": "High CPU",
        "metric_name": "cpu.usage",
        "operator": ">",
        "threshold": 80.0,
        "duration_seconds": 300,
        "host": None,
        "tag_filter": {"core": None},
        "enabled": True,
        "created_at": 1000.0,
        "updated_at": 1001.0,
    }


def test_created_rules_are_listed_and_fetchable_by_id():
    client = client_for()
    client.post("/rules", json=rule_body(name="a"))
    client.post("/rules", json=rule_body(name="b", host="web-1"))

    listed = client.get("/rules").json()["rules"]

    assert [(r["id"], r["name"], r["host"]) for r in listed] == [(1, "a", None), (2, "b", "web-1")]
    assert client.get("/rules/2").json()["name"] == "b"


def test_invalid_rule_is_rejected_with_field_level_detail():
    response = client_for().post("/rules", json=rule_body(operator="==", duration_seconds=-1))

    assert response.status_code == 422
    locations = {tuple(e["loc"]) for e in response.json()["detail"]}
    assert {("body", "operator"), ("body", "duration_seconds")} <= locations


@pytest.mark.parametrize("method", ["post", "put"])
def test_a_rule_for_an_unknown_host_is_a_422_on_the_host_field(method):
    client = client_for()
    client.post("/rules", json=rule_body())
    url = "/rules" if method == "post" else "/rules/1"

    response = getattr(client, method)(url, json=rule_body(host="ghost"))

    assert response.status_code == 422
    (error,) = response.json()["detail"]
    assert error["loc"] == ["body", "host"]
    assert "Unknown host: ghost" in error["msg"]


def test_update_replaces_the_whole_rule():
    client = client_for()
    client.post("/rules", json=rule_body())

    response = client.put("/rules/1", json=rule_body(name="Hot", threshold=95, host="web-1", enabled=False))

    assert response.status_code == 200
    body = response.json()
    assert (body["id"], body["name"], body["threshold"], body["host"], body["enabled"]) == (1, "Hot", 95.0, "web-1", False)
    assert client.get("/rules/1").json() == body


def test_delete_removes_the_rule():
    client = client_for()
    client.post("/rules", json=rule_body())

    response = client.delete("/rules/1")

    assert response.status_code == 204
    assert response.content == b""
    assert client.get("/rules").json() == {"rules": []}


@pytest.mark.parametrize(
    "method, url",
    [("get", "/rules/99"), ("put", "/rules/99"), ("delete", "/rules/99")],
)
def test_unknown_rule_is_a_404(method, url):
    kwargs = {"json": rule_body()} if method == "put" else {}

    response = getattr(client_for(), method)(url, **kwargs)

    assert response.status_code == 404
    assert response.json() == {"detail": "Unknown rule: 99"}


@pytest.mark.parametrize("error", [psycopg.OperationalError("down"), PoolTimeout("busy")])
@pytest.mark.parametrize(
    "method, url",
    [("get", "/rules"), ("get", "/rules/1"), ("post", "/rules"), ("put", "/rules/1"), ("delete", "/rules/1")],
)
def test_database_outage_is_a_503(error, method, url):
    kwargs = {"json": rule_body()} if method in ("post", "put") else {}

    response = getattr(client_for(error=error), method)(url, **kwargs)

    assert response.status_code == 503


def test_rule_endpoints_need_no_api_key():
    response = client_for().post("/rules", json=rule_body())

    assert response.status_code == 201


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
def test_browser_preflight_allows_editing_and_deleting_rules(method):
    response = client_for().options(
        "/rules/1",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": "Content-Type",
        },
    )

    assert response.status_code == 200
    assert method in response.headers["access-control-allow-methods"]
