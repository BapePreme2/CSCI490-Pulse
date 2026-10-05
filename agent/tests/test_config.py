import pytest

from pulse_agent.config import ConfigError, load_config


def test_load_valid_config(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "endpoint: https://example.com\n"
        "api_key: secret\n"
        "interval_seconds: 30\n"
        "environment: prod\n"
        "tags:\n"
        "  team: platform\n"
    )

    config = load_config(config_file)

    assert config.endpoint == "https://example.com"
    assert config.api_key == "secret"
    assert config.interval_seconds == 30.0
    assert config.environment == "prod"
    assert config.tags == {"team": "platform"}
    assert config.hostname


def test_missing_required_fields_raises(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("interval_seconds: 5\n")

    with pytest.raises(ConfigError):
        load_config(config_file)


def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "does_not_exist.yaml")


def test_non_mapping_config_raises(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("- just\n- a\n- list\n")

    with pytest.raises(ConfigError):
        load_config(config_file)


@pytest.mark.parametrize("endpoint", ["ingest.example.com/metrics", "ftp://example.com", "localhost:8000"])
def test_endpoint_must_be_an_http_url(tmp_path, endpoint):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(f"endpoint: {endpoint}\napi_key: secret\n")

    with pytest.raises(ConfigError, match="http"):
        load_config(config_file)


def test_request_timeout_is_configurable_and_must_be_positive(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("endpoint: https://e.com\napi_key: k\nrequest_timeout_seconds: 3\n")
    assert load_config(config_file).request_timeout_seconds == 3.0

    config_file.write_text("endpoint: https://e.com\napi_key: k\nrequest_timeout_seconds: 0\n")
    with pytest.raises(ConfigError):
        load_config(config_file)


def test_defaults_are_applied(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("endpoint: https://example.com\napi_key: secret\n")

    config = load_config(config_file)

    assert config.interval_seconds == 10.0
    assert config.environment is None
    assert config.tags == {}
    assert config.buffer_max_batches == 100


def test_environment_is_coerced_to_a_string(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("endpoint: https://e.com\napi_key: k\nenvironment: 7\n")

    assert load_config(config_file).environment == "7"


def test_tags_cannot_include_host(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("endpoint: https://e.com\napi_key: k\ntags:\n  host: spoofed\n")

    with pytest.raises(ConfigError, match="host"):
        load_config(config_file)


def test_tags_cannot_include_environment(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("endpoint: https://e.com\napi_key: k\ntags:\n  environment: prod\n")

    with pytest.raises(ConfigError, match="environment"):
        load_config(config_file)


def test_tags_must_be_a_mapping(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("endpoint: https://e.com\napi_key: k\ntags:\n  - not\n  - a\n  - mapping\n")

    with pytest.raises(ConfigError, match="tags"):
        load_config(config_file)


def test_tags_reject_an_empty_key(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text('endpoint: https://e.com\napi_key: k\ntags:\n  "": value\n')

    with pytest.raises(ConfigError, match="empty"):
        load_config(config_file)


def test_tags_values_are_coerced_to_strings(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("endpoint: https://e.com\napi_key: k\ntags:\n  replicas: 3\n")

    assert load_config(config_file).tags == {"replicas": "3"}
