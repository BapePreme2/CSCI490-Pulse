from fastapi.testclient import TestClient

from pulse_ingest import __version__
from pulse_ingest.app import create_app


def test_health_returns_ok_and_version():
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_unknown_route_is_404():
    client = TestClient(create_app())

    assert client.get("/nope").status_code == 404
