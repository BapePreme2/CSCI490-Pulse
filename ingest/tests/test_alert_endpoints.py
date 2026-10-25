"""GET /alerts: the alert history the dashboard's Alerts page reads."""

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg_pool import PoolTimeout

from pulse_ingest.app import create_app
from pulse_ingest.store import Alert, AlertHistory
from tests.fakes import FakeStore


def resolved_alert(**overrides):
    fields = dict(
        id=7, rule_id=3, host_id=1, hostname="web-1", unit="percent", tags={"core": "0"},
        rule_name="High CPU", metric_name="cpu.usage", operator=">", threshold=80.0,
        duration_seconds=300, state="resolved", pending_since=1000.0, fired_at=1300.0,
        resolved_at=1725.0, resolve_reason="recovered", last_value=41.0, last_evaluated_at=1725.0,
    )
    fields.update(overrides)
    return Alert(**fields)


def client_for(history=None, error=None):
    store = FakeStore(error=error, alert_history=history)
    return store, TestClient(create_app(api_keys={"k"}, store=store))


def test_alerts_is_empty_when_nothing_has_fired():
    store, client = client_for()

    response = client.get("/alerts")

    assert response.status_code == 200
    assert response.json() == {"alerts": [], "has_more": False, "firing_count": 0}
    assert store.alert_queries == [(None, None, 100)]


def test_alerts_lists_each_alert_with_its_times_and_outcome():
    firing = resolved_alert(id=8, rule_id=None, state="firing", resolved_at=None, resolve_reason=None, last_value=95.5)
    _, client = client_for(AlertHistory([firing, resolved_alert()], has_more=True, firing_count=1))

    body = client.get("/alerts").json()

    assert (body["has_more"], body["firing_count"]) == (True, 1)
    assert body["alerts"][1] == {
        "id": 7,
        "rule_id": 3,
        "rule_name": "High CPU",
        "host": "web-1",
        "metric_name": "cpu.usage",
        "unit": "percent",
        "tags": {"core": "0"},
        "operator": ">",
        "threshold": 80.0,
        "duration_seconds": 300,
        "state": "resolved",
        "pending_since": 1000.0,
        "fired_at": 1300.0,
        "resolved_at": 1725.0,
        "resolve_reason": "recovered",
        "last_value": 41.0,
        "last_evaluated_at": 1725.0,
    }
    first = body["alerts"][0]
    assert (first["state"], first["rule_id"], first["resolved_at"], first["resolve_reason"]) == (
        "firing", None, None, None
    )


def test_filters_and_limit_are_passed_to_the_store():
    store, client = client_for()

    client.get("/alerts", params={"host": "web-1", "state": "firing", "limit": 250})

    assert store.alert_queries == [("web-1", "firing", 250)]


@pytest.mark.parametrize(
    "params", [{"state": "pending"}, {"state": "ok"}, {"limit": 0}, {"limit": 1001}, {"limit": "lots"}]
)
def test_bad_filters_are_a_422(params):
    store, client = client_for()

    response = client.get("/alerts", params=params)

    assert response.status_code == 422
    assert store.alert_queries == []


@pytest.mark.parametrize("error", [psycopg.OperationalError("down"), PoolTimeout("busy")])
def test_database_outage_is_a_503(error):
    _, client = client_for(error=error)

    assert client.get("/alerts").status_code == 503
