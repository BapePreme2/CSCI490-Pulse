import logging
import socket

import pytest

from pulse_agent.buffer import MetricBuffer
from pulse_agent.metrics import Metric
from pulse_agent.sender import HttpSender


def metrics(count=1):
    return [
        Metric(name="cpu.usage", value=float(i), unit="percent", tags={"core": str(i)}, timestamp=1789419042.5)
        for i in range(count)
    ]


def sender_for(server, **kwargs):
    return HttpSender(server.url, "secret-key", "web-1", **kwargs)


def test_posts_host_and_metrics_with_bearer_key(stub_server):
    assert sender_for(stub_server).send(metrics(2)) is True

    (request,) = stub_server.requests
    assert request["path"] == "/metrics"
    assert request["headers"]["Authorization"] == "Bearer secret-key"
    assert request["json"]["host"] == "web-1"
    assert request["json"]["metrics"][1] == {
        "name": "cpu.usage",
        "value": 1.0,
        "unit": "percent",
        "tags": {"core": "1"},
        "timestamp": 1789419042.5,
    }


def test_large_flushes_are_split_into_chunks(stub_server):
    assert sender_for(stub_server, chunk_size=1000).send(metrics(2500)) is True

    assert [len(r["json"]["metrics"]) for r in stub_server.requests] == [1000, 1000, 500]


def test_stops_and_reports_failure_when_a_later_chunk_fails(stub_server):
    stub_server.statuses = [202, 503]

    assert sender_for(stub_server, chunk_size=10).send(metrics(30)) is False

    assert len(stub_server.requests) == 2


@pytest.mark.parametrize("status", [500, 502, 503, 429, 408])
def test_server_errors_are_retryable(stub_server, status):
    stub_server.statuses = [status]

    assert sender_for(stub_server).send(metrics()) is False


@pytest.mark.parametrize("status", [401, 403, 404])
def test_auth_and_routing_problems_are_kept_for_retry(stub_server, status):
    stub_server.statuses = [status]

    assert sender_for(stub_server).send(metrics()) is False


def test_bad_api_key_points_at_the_config(stub_server, caplog):
    stub_server.statuses = [401]

    with caplog.at_level(logging.ERROR):
        sender_for(stub_server).send(metrics())

    assert "api_key" in caplog.text


@pytest.mark.parametrize("status", [400, 413, 422])
def test_permanently_rejected_batches_are_dropped_not_retried(stub_server, status, caplog):
    stub_server.statuses = [status]

    with caplog.at_level(logging.ERROR):
        assert sender_for(stub_server).send(metrics()) is True

    assert "dropping" in caplog.text


def test_redirects_are_not_followed_and_count_as_failure(stub_server):
    stub_server.statuses = [307]

    assert sender_for(stub_server).send(metrics()) is False

    assert len(stub_server.requests) == 1


def test_unreachable_server_is_retryable():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    sender = HttpSender(f"http://127.0.0.1:{port}/metrics", "k", "web-1", timeout=1)

    assert sender.send(metrics()) is False


def test_slow_server_times_out_and_is_retryable(stub_server):
    stub_server.delay = 1.0

    assert sender_for(stub_server, timeout=0.2).send(metrics()) is False


def test_warns_when_api_key_would_travel_over_plain_http(caplog):
    with caplog.at_level(logging.WARNING):
        HttpSender("http://ingest.example.com/metrics", "k", "web-1")

    assert "plaintext" in caplog.text


def test_no_warning_for_https_or_localhost(caplog):
    with caplog.at_level(logging.WARNING):
        HttpSender("https://ingest.example.com/metrics", "k", "web-1")
        HttpSender("http://localhost:8000/metrics", "k", "web-1")

    assert "plaintext" not in caplog.text


def test_buffer_retries_through_an_outage_and_delivers_once_recovered(stub_server):
    stub_server.statuses = [503, 503]
    sender = sender_for(stub_server)
    buffer = MetricBuffer(base_backoff_seconds=1.0)
    buffer.add(metrics(3))

    assert buffer.flush(sender, now=0.0) is False
    assert buffer.flush(sender, now=1.5) is False
    assert buffer.flush(sender, now=10.0) is True

    assert len(buffer) == 0
    assert len(stub_server.requests) == 3
    assert len(stub_server.requests[-1]["json"]["metrics"]) == 3
