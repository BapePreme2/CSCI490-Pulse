import pytest
from fastapi.testclient import TestClient

from pulse_ingest.app import create_app
from pulse_ingest.auth import extract_bearer_token, is_valid_key, load_api_keys

BODY = {
    "host": "web-1",
    "metrics": [{"name": "cpu.usage", "value": 1.0, "unit": "percent", "timestamp": 1789419042.5}],
}


def post(client, headers=None, **kwargs):
    return client.post("/metrics", json=BODY, headers=headers or {}, **kwargs)


# --- helpers ---


@pytest.mark.parametrize(
    "header, expected",
    [
        ("Bearer abc123", "abc123"),
        ("bearer abc123", "abc123"),
        ("Bearer   abc123  ", "abc123"),
        ("Basic abc123", None),
        ("Bearer", None),
        ("Bearer ", None),
        ("abc123", None),
        ("", None),
        (None, None),
    ],
)
def test_extract_bearer_token(header, expected):
    assert extract_bearer_token(header) == expected


def test_is_valid_key():
    assert is_valid_key("k2", ["k1", "k2"])
    assert not is_valid_key("nope", ["k1", "k2"])
    assert not is_valid_key("k1", [])
    assert not is_valid_key("k1", ["k1x"])
    assert is_valid_key("clé", ["clé"])


def test_load_api_keys_parses_comma_separated(monkeypatch):
    monkeypatch.setenv("PULSE_API_KEYS", "a, b ,,c")

    assert load_api_keys() == {"a", "b", "c"}


def test_load_api_keys_empty_when_unset(monkeypatch):
    monkeypatch.delenv("PULSE_API_KEYS", raising=False)

    assert load_api_keys() == frozenset()


# --- middleware behavior ---


def test_valid_key_is_accepted():
    client = TestClient(create_app(api_keys={"good"}))

    assert post(client, {"Authorization": "Bearer good"}).status_code == 202


def test_any_configured_key_works():
    client = TestClient(create_app(api_keys={"one", "two"}))

    assert post(client, {"Authorization": "Bearer two"}).status_code == 202


def test_missing_key_is_401_with_challenge():
    client = TestClient(create_app(api_keys={"good"}))

    response = post(client)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("header", ["Bearer wrong", "Basic good", "good", "Bearer "])
def test_wrong_or_malformed_credentials_are_401(header):
    client = TestClient(create_app(api_keys={"good"}))

    assert post(client, {"Authorization": header}).status_code == 401


def test_no_configured_keys_rejects_everything():
    client = TestClient(create_app(api_keys=set()))

    assert post(client, {"Authorization": "Bearer anything"}).status_code == 401
    assert post(client).status_code == 401


def test_auth_runs_before_body_validation():
    client = TestClient(create_app(api_keys={"good"}))

    bad_json = client.post(
        "/metrics", content="{not json", headers={"Content-Type": "application/json"}
    )
    bad_schema = client.post("/metrics", json={"host": ""})

    assert bad_json.status_code == 401
    assert bad_schema.status_code == 401


def test_trailing_slash_does_not_bypass_auth():
    client = TestClient(create_app(api_keys={"good"}))

    response = client.post("/metrics/", json=BODY, follow_redirects=False)

    assert response.status_code == 401


def test_health_stays_open_without_a_key():
    client = TestClient(create_app(api_keys={"good"}))

    assert client.get("/health").status_code == 200


def test_default_app_reads_keys_from_environment(monkeypatch):
    monkeypatch.setenv("PULSE_API_KEYS", "from-env")
    client = TestClient(create_app())

    assert post(client, {"Authorization": "Bearer from-env"}).status_code == 202
    assert post(client, {"Authorization": "Bearer other"}).status_code == 401
