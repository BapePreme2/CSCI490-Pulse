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
```

Until the ingestion API exists (Week 4), the agent reports through a
`LoggingSender` that logs each batch instead of sending it over HTTP, so it
runs and is demoable standalone.

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
- **`Sender`** is a small protocol (`send(metrics) -> bool`) so the week 4
  HTTP client (`AGENT-11`) can be dropped in without touching the
  collection or buffering code.

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
