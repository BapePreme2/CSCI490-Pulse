# Pulse Agent

Lightweight host monitoring agent. Collects CPU, load average, memory,
disk, and network metrics on an interval and reports them to the Pulse
ingestion API.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Run

```bash
cp config.example.yaml config.yaml   # then fill in endpoint/api_key
pulse-agent --config config.yaml           # runs forever on interval_seconds
pulse-agent --config config.yaml --once    # single collect+flush cycle, then exit
pulse-agent --config config.yaml --dry-run # log batches instead of sending them
```

`endpoint` in the config is the full URL of the ingestion API's
`POST /metrics`, and `api_key` must be one of the keys the API was started
with (`PULSE_API_KEYS`). Use `--dry-run` to try the agent without a server.

## Delivery

`pulse_agent/sender.py` (`HttpSender`) posts each flush to the ingestion API
as `{"host": ..., "metrics": [...]}` with an `Authorization: Bearer` header,
in requests of at most 1000 metrics. How the response is handled:

| Result | Behavior |
| --- | --- |
| `2xx` | Delivered. |
| Network error, timeout, `5xx`, `429`, `401`/`403`/`404` | Kept in the buffer and retried with exponential backoff. `401`/`403` also log an error pointing at `api_key`. |
| `400`, `413`, `422` | The server will never accept this payload, so it is logged and dropped. Otherwise one bad metric would block every later batch. |
| Redirect (`3xx`) | Treated as a failure and not followed, since following one turns the POST into a GET and would silently lose data. |

If a later chunk of a large flush fails, the whole flush is retried. That is
safe because the API deduplicates samples, so resent data is not stored
twice. The agent logs a warning if the endpoint is plain `http://` to a
non-local host, since the API key would travel unencrypted.

## Test

```bash
pytest
```

## Design notes

- **Rate metrics** (disk I/O, network) are derived from psutil's
  cumulative-since-boot counters, so a collector needs two samples to
  produce a rate. The first `collect()` call on `DiskIOCollector`,
  `NetworkCollector`, and `CPUCollector` (which has the same priming
  requirement for `psutil.cpu_percent`) returns no metrics.
- **`MetricBuffer`** batches collected metrics and retries failed sends
  with exponential backoff (capped at `max_backoff_seconds`). If the
  ingestion API is down long enough to exceed `buffer_max_batches`, the
  oldest metrics are dropped to keep the agent's memory bounded.
- **`Sender`** is a small protocol (`send(metrics) -> bool`), so the
  collection and buffering code doesn't know how metrics are delivered.
  `HttpSender` is the real implementation; `LoggingSender` backs `--dry-run`.

## Task mapping (Week 3, CSCI 490 semester plan)

| Task | File(s) |
| --- | --- |
| AGENT-01 scaffold | this directory: `pyproject.toml`, `pulse_agent/__main__.py`, `config.example.yaml` |
| AGENT-02 CPU | `pulse_agent/collectors/cpu.py` |
| AGENT-03 load average | `pulse_agent/collectors/load.py` |
| AGENT-04 memory | `pulse_agent/collectors/memory.py` |
| AGENT-05 disk usage | `pulse_agent/collectors/disk.py` (`DiskUsageCollector`) |
| AGENT-06 disk I/O | `pulse_agent/collectors/disk.py` (`DiskIOCollector`) |
| AGENT-07 network | `pulse_agent/collectors/network.py` |
| AGENT-08 config loader | `pulse_agent/config.py` |
| AGENT-09 batching/retry buffer | `pulse_agent/buffer.py` |
| AGENT-10 unit tests | `tests/` |

## Task mapping (Week 4)

| Task | File(s) |
| --- | --- |
| AGENT-11 HTTP client | `pulse_agent/sender.py`, `tests/test_sender.py`, `tests/test_agent.py` |
