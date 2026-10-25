# OPS-03: running the MVP against a test fleet

The MVP (agent → ingest API → Postgres → alerting → email/Slack →
dashboard) had only ever run with one agent on the development machine.
OPS-03 ran it against a fleet of four hosts with faults injected, fixed what
broke, and ran it again to confirm the fixes. This is the record of both
runs, and how to repeat them.

## The fleet

| Host | What it is |
| --- | --- |
| `fleet-1`, `fleet-2`, `fleet-3` | The agent in its own Docker container each (`agent/Dockerfile`), started by `scripts/test-fleet.sh up 3`. Separate hostname, filesystem, and network interface; reaches the API through Docker's bridge network, like a remote host would. |
| `wsl-host` | The agent running natively on the development machine (WSL2, Ubuntu 24.04). |

All four reported every 5s (half the default, to fit more cycles into the
run). The API ran with alert evaluation every 2s and both notification
channels pointed at local stand-ins (a real SMTP server and a real HTTP
webhook receiver, from `pulse_ingest/smoke.py`), so every notification was
captured and counted.

Two rules were active the whole time:

- **Root disk in use**: `disk.usage > 1` for 10s on `mount = /`, fleet-wide.
  Always true, so every host has a firing alert to keep track of through
  the faults.
- **CPU pegged**: overall `cpu.usage > 95` for 30s. Never true; there to
  make sure nothing fires that shouldn't.

Faults, in order: stop a host (`docker stop`), restart the API while hosts
keep reporting, freeze a host (`docker pause`, the process is alive but does
nothing, like a hung VM), and bring the stopped host back.

## What the first run found

Four bugs, none of which the existing tests could have caught, because each
needed a second host, a container, or a restart under load to show up.

### 1. `docker stop` took 10 seconds and killed the agent (fixed)

`docker stop` on a fleet host took **10.2s** and ended in exit code **137**
(SIGKILL). The agent never installed a `SIGTERM` handler; on a normal
machine that just means an abrupt exit, but as PID 1 in a container an
unhandled `SIGTERM` is *ignored*, so Docker waited out its 10s grace period
and killed it. The same applies to `systemctl stop`.

**Fix:** the CLI handles `SIGTERM` and `SIGINT`, and `run_forever()` waits
on an event instead of `time.sleep`, so it returns at once between cycles
and logs how many buffered metrics (if any) it couldn't deliver. Tests:
`tests/test_agent.py` sends both signals to a real agent process.

### 2. Containers reported fake disks and no root disk (fixed)

Inside a container, `disk.usage` had three series, for `/etc/hostname`,
`/etc/hosts`, and `/etc/resolv.conf`, and none for `/`. Docker bind-mounts
those three *files* into every container, and psutil lists them as mounts of
the host's disk; the container's real root filesystem is an overlay, which
psutil's `all=False` leaves out. So "Root disk in use" fired on `wsl-host`
and never on any container.

**Fix:** `DiskUsageCollector` skips mountpoints that are files and always
includes `/`, overlay or not. Tests: `tests/test_disk.py`.

### 3. More than half of every host's data was zeros (fixed)

| Host | Series per report | From `loop*`/`ram*` devices |
| --- | --- | --- |
| `wsl-host` | 182 | 96 |
| `fleet-1` | 148 | 96 |

`DiskIOCollector` reported 4 metrics for every kernel block device,
including 8 loop devices and 16 ramdisks that always read 0, and every
container sees the host's. That was 53-65% of all rows written, for nothing.

**Fix:** `loop*` and `ram*` devices are skipped (`zram`, which is real swap,
is kept). Tests: `tests/test_disk.py`.

### 4. Restarting the API re-fired every firing alert (fixed)

The API was stopped for 20s and started again. Before: alert #1 firing on
`wsl-host`. After: alert #1 **resolved (not evaluated)** and a new alert #2
**firing**, for the same series, 2s apart. With notifications on, that is a
"this is NOT a recovery, the host may have stopped reporting" email and a
new "FIRING" email per firing alert, every time the API restarts.

Cause: while the API was down the agents kept collecting and retrying, with
backoff that had reached 8s by then (it goes up to 60s). When the API came
back, every series' newest stored sample was over 30s old, so the first
evaluation cycle treated every host as gone quiet and closed its alerts.
Two seconds later the buffered samples arrived and the alerts fired again.
The existing restart test only covered a restart with no data gap.

**Fix:** for `CATCH_UP_SECONDS` (90s) after the evaluator starts, or after
a cycle fails because the database is down, an *enabled* rule's alert
that isn't evaluated is held as it is instead of resolved. A disabled or
deleted rule's alerts still close at once. See "A restart doesn't count as
the hosts going quiet" in `ingest/README.md`. Tests: `tests/test_alerts.py`
and `tests/test_alert_store.py` (the restart-with-a-gap case, a host that
really stays silent, and disabling a rule right after a restart).

### What worked the first time

- **No data lost through an API outage.** Across the 20s restart, the
  largest gap between any host's stored samples was 5.8s (one 5s interval):
  every agent buffered and redelivered everything it collected.
- **Offline detection.** A host frozen with `docker pause` showed as
  offline 27s in (the 30s staleness window, less the 3s since its last
  report) and online again 0.5s after it was unpaused.

## The run after the fixes

The same fleet and rules, on a fresh database, with the faults on a
timeline (seconds from the start of the run):

| When | Fault | What happened |
| --- | --- | --- |
| 0-46s | Fleet starts | "Root disk in use" fired on all four hosts, the containers included (`device=overlay`, `mount=/`). |
| 61s | `docker stop fleet-3` | Took **0.2s**, exit code **0** (was 10.2s and 137). Offline at 89s; its alert resolved as not evaluated at 92s. |
| 151-191s | API stopped for **40s**, then started | Alerts #1, #2, #3 firing before; the same #1, #2, #3 firing after, and 100s later. Nothing resolved or re-fired. |
| 300-360s | `docker pause fleet-2` for 60s | Offline at 330s; alert #3 resolved as not evaluated at 331s. Online again 0.4s after unpausing; a new alert (#5) fired at 372s, once its 10s duration was met. |
| 421s | `docker start fleet-3` | Online within 1s; a new alert (#6) fired at 432s. |

Across the whole run:

- **Notifications matched the transitions exactly:** 8 transitions logged
  (6 fires, 2 resolves), 8 emails, 8 Slack messages, the same 8 in the same
  order. No duplicates, nothing for pending steps, and "CPU pegged" never
  fired.
- **No data lost.** The only gaps over two intervals in any host's samples
  were the two intended ones (`fleet-2`'s 61s pause and `fleet-3`'s 362s
  stop). The 40s API outage left none: the agents' backlog filled it.
- **Less data for the same picture:** 48 series per report from each
  container (was 148) and 92 from `wsl-host` (was 182). 17,825 rows in
  8 minutes for four hosts.
- **Nothing crashed or complained.** No `ERROR` or traceback in any API or
  agent log, no container restarts, and the API's memory went from 59 MB to
  65 MB over the run (with a restart in the middle), with no steady climb.
- **One thing to know when demoing:** right after an API restart, the
  fleet page shows every host offline for 7-12s, until each agent's next
  retry lands. That's the page being accurate (the API hasn't heard from
  them in a while), and alerts are no longer affected by it.

## Not fixed, for later

- **Nothing alerts when a healthy host goes silent.** A host that stops
  reporting shows as offline on the dashboard, and any alert that was
  *already firing* on it resolves as "not evaluated" (whose notification
  says the host may have stopped reporting). But a host that dies while all
  its alerts are OK produces no notification at all. A built-in "host
  offline for N seconds" alert would close that gap; "Agent heartbeat age"
  is already a Core metric in the catalog.
- **Container CPU and memory are the machine's.** psutil reads `/proc`,
  which containers share with the machine they run on, so all three
  container hosts report the same CPU, load, and memory. Disk and network
  are per-container. Fine for a test fleet, and documented in
  `agent/DEPLOY.md`; a real fleet runs the agent on each machine.
- **The API listens on `0.0.0.0` for the fleet.** Containers reach the API
  through Docker's bridge, so it has to listen there, not only on
  `127.0.0.1`. On WSL2 that's still only reachable from this machine, but
  on a machine with a LAN address it exposes the API (which has no auth on
  its dashboard and rule endpoints) to the network.
- **No second physical machine yet.** The containers share this machine's
  kernel, so the real test is the agent on another computer
  (`agent/DEPLOY.md`; psutil supports macOS too) reporting to this API.
  Under WSL2 that needs Windows to forward a port to WSL (or WSL's mirrored
  networking mode), which needs admin rights.

## Running it yourself

```bash
scripts/dev-db.sh up
cd ingest && source .venv/bin/activate
export PULSE_API_KEYS=fleet-key
uvicorn pulse_ingest.app:app --host 0.0.0.0     # in one terminal
cd .. && PULSE_API_KEY=fleet-key scripts/test-fleet.sh up 3
scripts/test-fleet.sh status
```

The dashboard (`cd dashboard && npm run dev`) shows `fleet-1`..`fleet-3` on
the fleet page within a few seconds. Then, to see each fault:

| To see | Run | Expect |
| --- | --- | --- |
| A host dying | `scripts/test-fleet.sh stop fleet-2` | Stops in under a second. Offline on the fleet page within ~30s; its firing alerts resolve as "No longer evaluated". |
| A host hanging | `scripts/test-fleet.sh pause fleet-2`, later `unpause fleet-2` | The same, then online again within a few seconds of unpausing. |
| An API restart | Ctrl-C the API, wait, start it again | Nothing resolves or re-fires; the agents' buffered data fills in the gap. |
| A host coming back | `scripts/test-fleet.sh start fleet-2` | Online at once; its alerts fire again once their duration is met. |

`scripts/test-fleet.sh down` removes the whole fleet.
