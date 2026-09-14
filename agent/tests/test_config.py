import pytest

from pulse_agent.config import ConfigError, load_config


def test_load_valid_config(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "endpoint: https://example.com\n"
        "api_key: secret\n"
        "interval_seconds: 30\n"
        "tags:\n"
        "  environment: prod\n"
    )

    config = load_config(config_file)

    assert config.endpoint == "https://example.com"
    assert config.api_key == "secret"
    assert config.interval_seconds == 30.0
    assert config.tags == {"environment": "prod"}
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


def test_defaults_are_applied(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("endpoint: https://example.com\napi_key: secret\n")

    config = load_config(config_file)

    assert config.interval_seconds == 10.0
    assert config.tags == {}
    assert config.buffer_max_batches == 100
