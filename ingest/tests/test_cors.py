from fastapi.testclient import TestClient

from pulse_ingest.app import create_app
from tests.fakes import FakeStore


def make_client(origins):
    return TestClient(
        create_app(api_keys={"k"}, store=FakeStore(hosts={"web-1": {"latest": []}}), cors_origins=origins)
    )


def test_allowed_origin_gets_the_cors_header_on_a_real_response():
    client = make_client(["http://localhost:5173"])

    response = client.get("/hosts/web-1/metrics/latest", headers={"Origin": "http://localhost:5173"})

    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_disallowed_origin_gets_no_cors_header():
    client = make_client(["http://localhost:5173"])

    response = client.get("/hosts/web-1/metrics/latest", headers={"Origin": "http://evil.example.com"})

    assert "access-control-allow-origin" not in response.headers


def test_error_responses_still_carry_cors_headers():
    client = make_client(["http://localhost:5173"])

    response = client.get("/hosts/nope/metrics/latest", headers={"Origin": "http://localhost:5173"})

    assert response.status_code == 404
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_preflight_request_is_answered_without_needing_an_api_key():
    client = make_client(["http://localhost:5173"])

    response = client.options(
        "/metrics",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_default_origins_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("PULSE_CORS_ORIGINS", "http://example.com, http://example.org")
    client = TestClient(
        create_app(api_keys={"k"}, store=FakeStore(hosts={"web-1": {"latest": []}}))
    )

    allowed = client.get("/hosts/web-1/metrics/latest", headers={"Origin": "http://example.org"})
    other = client.get("/hosts/web-1/metrics/latest", headers={"Origin": "http://not-listed.com"})

    assert allowed.headers["access-control-allow-origin"] == "http://example.org"
    assert "access-control-allow-origin" not in other.headers
