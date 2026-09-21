import logging

from pulse_agent.__main__ import main
from pulse_agent.agent import PulseAgent
from pulse_agent.config import AgentConfig
from pulse_agent.metrics import Metric
from pulse_agent.sender import HttpSender


class StubCollector:
    def collect(self):
        return [Metric(name="cpu.usage", value=5.0, unit="percent", tags={"core": "0"})]


class RecordingSender:
    def __init__(self, succeed=True):
        self.succeed = succeed
        self.batches = []

    def send(self, metrics):
        self.batches.append(list(metrics))
        return self.succeed


def make_agent(sender, **config_overrides):
    config = AgentConfig(
        endpoint="http://127.0.0.1:1/metrics",
        api_key="k",
        hostname="web-1",
        tags={"environment": "dev"},
        **config_overrides,
    )
    agent = PulseAgent(config, sender=sender)
    agent.collectors = [StubCollector()]
    return agent


def test_metrics_are_tagged_with_host_and_configured_tags():
    sender = RecordingSender()
    make_agent(sender).run_once()

    (metric,) = sender.batches[0]
    assert metric.tags == {"core": "0", "host": "web-1", "environment": "dev"}


def test_failed_send_keeps_metrics_buffered_for_retry():
    agent = make_agent(RecordingSender(succeed=False))

    agent.run_once()

    assert len(agent.buffer) == 1


def test_default_sender_is_the_http_client():
    config = AgentConfig(endpoint="https://ingest.example.com/metrics", api_key="k", hostname="web-1")

    assert isinstance(PulseAgent(config).sender, HttpSender)


def test_end_to_end_against_a_real_http_server(stub_server):
    config = AgentConfig(endpoint=stub_server.url, api_key="k", hostname="web-1")
    agent = PulseAgent(config)
    agent.collectors = [StubCollector()]

    agent.run_once()

    (request,) = stub_server.requests
    assert request["json"]["host"] == "web-1"
    assert request["json"]["metrics"][0]["name"] == "cpu.usage"
    assert len(agent.buffer) == 0


def test_dry_run_logs_instead_of_sending(stub_server, tmp_path, caplog):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(f"endpoint: {stub_server.url}\napi_key: k\n")

    with caplog.at_level(logging.INFO):
        exit_code = main(["--config", str(config_file), "--once", "--dry-run"])

    assert exit_code == 0
    assert "Would send" in caplog.text
    assert stub_server.requests == []


def test_missing_config_exits_with_error(tmp_path, capsys):
    exit_code = main(["--config", str(tmp_path / "nope.yaml"), "--once"])

    assert exit_code == 1
    assert "Config error" in capsys.readouterr().err
