# Pulse Agent

Lightweight host monitoring agent. Collects CPU, load average, memory,
disk, and network metrics, plus the host's listening ports, on an interval
and reports them to the Pulse ingestion API.

This README is for developing the agent. To put it on a machine you want
monitored, see [`DEPLOY.md`](DEPLOY.md) instead.

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

`SIGTERM` (what `docker stop`, systemd, and `kill` send) and `SIGINT`
(Ctrl-C) stop it cleanly: it exits 0 at once if it's waiting between
cycles, and logs how many buffered metrics, if any, never got delivered.

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

## Labeling

Every metric is tagged with `host` (from `hostname`, or the machine's real
hostname if omitted) and, if set, `environment` -- both applied last in
`PulseAgent.collect_once()` so they can never be overridden by a crafted
`tags` entry, even bypassing `from_dict`'s own validation of this (e.g. a
config built directly rather than loaded from YAML). `tags` is for anything
else and is validated at config-load time: it must be a mapping, every key
must be non-empty, values are coerced to strings, and it cannot contain
`host` or `environment` -- those have their own fields specifically so a
typo inside `tags` can't silently create a second, conflicting label.

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
- **Disks worth reporting.** `DiskUsageCollector` reports psutil's real
  devices, minus mountpoints that are files (Docker bind-mounts
  `/etc/hostname`, `/etc/hosts`, and `/etc/resolv.conf` into every
  container, and psutil lists them as mounts of the host's disk), plus the
  root filesystem `/` even when it's an overlay, as it is in a container.
  `DiskIOCollector` skips `loop*` and `ram*` block devices: they report
  zeros, and every container sees the host's, so they were more than half
  of a host's series. All three were found running the agent in Docker
  containers for OPS-03.
- **`Sender`** is a small protocol (`send(metrics) -> bool`), so the
  collection and buffering code doesn't know how metrics are delivered.
  `HttpSender` is the real implementation; `LoggingSender` backs `--dry-run`.
- **Listening ports** (`ListeningPortsCollector`) are sent as one
  `network.listening_port` series per socket (value 1, tagged `protocol`,
  `address`, `port`, and `process` when visible), plus a
  `network.listening_ports` count every cycle with the same timestamp. The
  API only stores numbers, so this needed no ingest or schema changes, and
  a reader tells which ports are still open by matching the latest count's
  timestamp. Seeing every socket's process takes root: as a normal user,
  Linux lists all ports but only names your own processes, and macOS
  (where psutil can't list all sockets unprivileged) falls back to your own
  processes' ports.
- **Exposed services** (SEC-02). A listening port is tagged
  `exposed: "true"` when it's in `sensitive_ports` (config; defaults to
  SSH, RDP, Telnet, FTP, SMTP, SMB, VNC, the Docker API, and common
  databases/caches, not web ports) and bound to an address other machines
  can reach. That's any non-loopback address, not only `0.0.0.0`/`::`,
  since binding to one LAN IP exposes a service to that LAN just the same.
  `network.exposed_services` counts them every cycle (IPv4 and IPv6 binds
  of one port count once), so an ordinary threshold rule,
  `network.exposed_services > 0`, alerts on it. This only sees what the
  host binds to: a firewall or cloud security group may still block it.

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

## Task mapping (Week 6)

| Task | File(s) |
| --- | --- |
| AGENT-12 hostname/tag/environment labeling | `pulse_agent/config.py` (`environment` field, `_validate_tags`), `pulse_agent/agent.py`, `config.example.yaml`, `tests/test_config.py`, `tests/test_agent.py` |
| OPS-01 install/run instructions for a new host | `DEPLOY.md` |

## Task mapping (Week 8)

| Task | File(s) |
| --- | --- |
| OPS-03 stabilize against a test fleet | `Dockerfile`, `.dockerignore`, `pulse_agent/__main__.py` + `pulse_agent/agent.py` (clean stop on `SIGTERM`/`SIGINT`), `pulse_agent/collectors/disk.py` (container root, file mounts, virtual devices), `tests/test_agent.py`, `tests/test_disk.py`; `scripts/test-fleet.sh` and `docs/ops-03-stability-run.md` at the repo root |

## Task mapping (Week 10)

| Task | File(s) |
| --- | --- |
| SEC-01 listening-port scan per host | `pulse_agent/collectors/ports.py`, `pulse_agent/agent.py`, `tests/test_ports.py`; dashboard `src/metrics/ports.ts`, `src/components/ListeningPorts.tsx` (on the host page) |
| SEC-02 flag exposed services | `pulse_agent/collectors/ports.py` (`DEFAULT_SENSITIVE_PORTS`, `network_reachable`), `pulse_agent/config.py` (`sensitive_ports`), `config.example.yaml`, `tests/test_ports.py`, `tests/test_config.py`; dashboard `src/components/ListeningPorts.tsx` (badge + warning), `src/components/RuleForm.tsx` (metric suggestions) |
