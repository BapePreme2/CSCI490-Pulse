# Pulse Ingest

Ingestion API and storage layer. Agents `POST` batches of metrics; the API
validates them and writes them to Postgres.

## Setup

```bash
scripts/dev-db.sh up                 # from the repo root: starts Postgres in Docker
cd ingest
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pulse_ingest.migrate       # applies migrations/*.sql
pytest                               # integration tests need the DB running
```

If the Docker container has stopped (e.g. after a reboot), `scripts/dev-db.sh
up` starts it again; the integration tests skip themselves when Postgres is
unreachable.

`tests/test_round_trip.py` is the end-to-end test: it launches the real API
and the real agent CLI as separate processes against a throwaway database. It
needs the agent installed too (`cd ../agent && python3 -m venv .venv && pip
install -e .`) and skips itself if `agent/.venv` is missing. It checks that:

- metrics from a running agent arrive in Postgres with sensible values,
  recent timestamps, and the configured tags;
- metrics the agent collects while the API is down are delivered after the
  API starts (the agent's retry buffer, across real processes);
- a wrong API key stores nothing while the agent keeps retrying;
- a rule fires from live agent data, and the API emails the fire and then
  the resolve to a throwaway local SMTP server, and posts both to a local
  stand-in for a Slack webhook. Notification settings in your own shell
  (`PULSE_SMTP_*`, `PULSE_ALERT_EMAIL_*`, `PULSE_SLACK_WEBHOOK_URL`) are
  ignored, so the tests never email a real inbox or post to a real channel.

For a quick check that a whole stack works, outside pytest, there's also
the [smoke test](#smoke-test-ops-02).

The database URL defaults to `postgresql://pulse:pulse@localhost:5432/pulse`
(dev-only credentials); override it with `PULSE_DATABASE_URL`.

## Run the API

```bash
cd ingest && source .venv/bin/activate
export PULSE_API_KEYS="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
uvicorn pulse_ingest.app:app --reload      # http://127.0.0.1:8000
curl http://127.0.0.1:8000/health          # {"status":"ok","version":"0.1.0"}
```

FastAPI's interactive docs are served at `/docs`. `GET /health` is a liveness
check only; it does not touch the database.

## Authentication

Agent requests (`POST /metrics`) must send `Authorization: Bearer <key>`,
where `<key>` is the `api_key` from the agent's config. Valid keys come from
the `PULSE_API_KEYS` environment variable (comma-separated, so keys can be
rotated by listing the old and new one together).

```bash
curl -X POST http://127.0.0.1:8000/metrics \
  -H "Authorization: Bearer $PULSE_API_KEYS" \
  -H "Content-Type: application/json" \
  -d '{"host":"web-1","metrics":[{"name":"cpu.usage","value":42.5,"unit":"percent","timestamp":1789419042.5}]}'
```

- Missing, malformed, or wrong keys get `401` with `WWW-Authenticate: Bearer`.
- The check runs as middleware before the body is read, so unauthenticated
  requests never get their payload parsed.
- Keys are compared in constant time.
- It fails closed: with no keys configured, every agent request is rejected
  (a warning is logged at startup).
- `/health` needs no key so load balancers and uptime checks can reach it.

## CORS (for the browser-based dashboard)

The dashboard runs on its own origin (`http://localhost:5173` in dev) and
calls this API directly from the browser, so the API must send CORS headers
or the browser silently blocks the dashboard's JavaScript from reading the
response -- the request still succeeds server-side, but `fetch()` in the
browser sees it as a network failure either way.

Allowed methods are `GET`, `POST`, `PUT`, and `DELETE` (the last two for
editing and deleting alert rules).

Allowed origins come from `PULSE_CORS_ORIGINS` (comma-separated), defaulting
to `http://localhost:5173`. Set it to the dashboard's real URL(s) in any
other environment.

`CORSMiddleware` is added *after* the API-key middleware in the code so that,
per Starlette's `add_middleware` (which inserts at the front of the
middleware list), it ends up outermost -- able to answer a browser's
preflight `OPTIONS` request directly, and to attach the CORS header to every
response, including `401`/`404`/`503` ones, so the dashboard's error states
render correctly too.

## Payload schema (`POST /metrics`)

Defined in `pulse_ingest/schemas.py` (`MetricBatch`, `MetricIn`). One request
is one agent flush; each metric matches the agent's `Metric.to_dict()`.

```json
{
  "host": "web-1",
  "metrics": [
    {
      "name": "cpu.usage",
      "value": 42.5,
      "unit": "percent",
      "tags": {"core": "0", "environment": "dev"},
      "timestamp": 1789419042.579
    }
  ]
}
```

Rules: 1-5000 metrics per batch (matches the agent's default buffer cap),
`value` must be finite, `timestamp` is Unix epoch seconds, at most 20 tags of
string key/value, and unknown fields are rejected.

Responses:

- `202 {"accepted": N, "stored": M}` when the whole batch is valid and
  written. `accepted` is the number of metrics received; `stored` is how many
  were new, so a resent batch reports `stored: 0`.
- `422` with FastAPI's field-level `detail` (e.g. `["body", "metrics", 1,
  "value"]` points at the bad metric) when any part of it is invalid. A batch
  is all-or-nothing: one bad metric rejects the request.
- `503` when the database is unreachable. Agents should treat this as
  retryable (unlike `422`, which will never succeed on retry).

## Read endpoints (for the dashboard)

None of these endpoints require an API key, since the dashboard is a
trusted internal client reading its own database, not an agent.

**`GET /hosts`** -- every known host for the fleet overview page, with:

- `status`: `"online"` if the host reported within the last
  `STALE_AFTER_SECONDS` (30s, a fixed constant in `store.py`), `"offline"`
  otherwise. Deliberately a generous fixed threshold rather than one
  derived per-host from each agent's own interval, to stay simple; it
  comfortably covers the packaged agent's default 10s `interval_seconds`
  while still flipping to offline within a demo-able amount of time if an
  agent is killed.
- Two "key metrics" computed in a single query (not one per host): the
  overall `cpu.usage` (the reading with no `core` tag -- not a requirement
  of zero tags altogether, since an agent's other configured tags, e.g.
  `environment`, land on every metric it reports) and a `memory_percent`
  derived from `memory.used`/`memory.total` (which never fan out, so any
  reading counts regardless of its tags). Either is `null` if that host has
  no such data yet.

```json
{
  "hosts": [
    {"hostname": "web-1", "first_seen_at": 1789415000.0, "last_seen_at": 1789419042.0,
     "status": "online", "cpu_usage": 42.5, "memory_percent": 61.2}
  ]
}
```

**A real bug this caught:** the first version of this query required tags
to be *exactly* `{}` to count as the "overall" cpu.usage/memory reading.
That's the same bug class DASH-04 hit on the frontend, just in SQL this
time: this project's own demo agent config sets `tags: {environment:
demo}`, which lands on every metric including the overall one, so the
exact-match filter never matched anything and `cpu_usage`/`memory_percent`
were silently `null` for any realistically configured agent. No test
caught it, since every fixture used bare, tag-free metrics. Found only by
running the real agent against the real API and noticing the fleet page
showed dashes it shouldn't have. Fixed by checking for the absence of the
`core` tag specifically (`NOT (v.tags ? 'core')`) instead of zero tags.

**`GET /hosts/{host}`** -- one host's own summary, the same shape as one
entry of `GET /hosts` above (hostname, first/last seen, `status`,
`cpu_usage`, `memory_percent`). `404` if the host is unknown. Implemented
as a thin filter over `list_hosts()` rather than a second copy of its
query, so the two can't drift out of sync the way the bug above could have
recurred if duplicated.

**`GET /hosts/{host}/metrics/latest`** -- the most recent value of every
series (metric + tags) the host has reported.

```bash
curl http://127.0.0.1:8000/hosts/web-1/metrics/latest
```

```json
{
  "host": "web-1",
  "metrics": [
    {"name": "cpu.usage", "unit": "percent", "tags": {"core": "0"}, "value": 12.5, "timestamp": 1789419042.0},
    {"name": "cpu.usage", "unit": "percent", "tags": {"core": "1"}, "value": 30.0, "timestamp": 1789419042.0}
  ]
}
```

`404` if the host has never reported. `200` with `"metrics": []` if it has
but currently has no samples.

**`GET /hosts/{host}/metrics/{name}`** -- every sample for one metric name
in a time range, grouped into one series per distinct tag set (so a
multi-core or multi-disk metric renders as separate chart lines without
extra client-side work).

```bash
curl "http://127.0.0.1:8000/hosts/web-1/metrics/cpu.usage?start=1789415000&end=1789419042"
```

```json
{
  "host": "web-1", "name": "cpu.usage", "start": 1789415000, "end": 1789419042,
  "series": [
    {"unit": "percent", "tags": {"core": "0"}, "points": [{"timestamp": 1789419042.0, "value": 12.5}]}
  ]
}
```

`start`/`end` are optional Unix-epoch-seconds query params; they default to
the last hour and are capped at a 30-day span (`400` if exceeded, or if
`start >= end`). `404` if the host is unknown; `200` with `"series": []` if
the host exists but has no data for that metric name. Both endpoints return
`503` if the database is unreachable.

## Write path

`pulse_ingest/store.py` (`PostgresMetricStore`) writes each batch in a single
transaction, so it is stored completely or not at all:

1. Upsert the host (`hosts.last_seen_at` is refreshed on every write, which
   later powers offline-host detection).
2. Upsert the batch's distinct `(name, unit)` pairs into `metrics`, in sorted
   order so concurrent batches can't deadlock on shared rows.
3. Insert all samples with `ON CONFLICT DO NOTHING`, using the unique sample
   key from the schema. This makes agent retries idempotent.

The agent already tags every metric with `host`; since `host_id` identifies
it, that tag is dropped before storing. Connections come from a pool
(`psycopg_pool`) that the app opens on startup. Run the migrations before
starting the API.

## Host self-registration

A host needs no separate setup step: its first-ever `POST /metrics` creates
its `hosts` row (step 1 of the write path above). Whether that write was a
brand-new host or an existing one checking in again is detected with the
standard Postgres `xmax = 0` idiom on the upsert's `RETURNING` clause --
`xmax` is left at 0 for a row the command just inserted, and set to the
current transaction for one the `ON CONFLICT DO UPDATE` branch touched
instead.

That result is surfaced two ways: the response gains a `new_host` field
(`202 {"accepted": N, "stored": M, "new_host": true|false}`), and the
server logs `New host registered: <hostname>` at INFO level the first
time. That log line needs the app to actually configure a logging handler
-- a plain `uvicorn module:app` entrypoint has no `__main__` of its own to
do that, and without it every `logger.info()` call in this app is silently
dropped (the root logger's default level is WARNING with no handler at
all). `pulse_ingest/app.py` calls `logging.basicConfig()` at import time to
fix that; it's a no-op if something else already configured the root
logger.

## Database schema

`migrations/001_initial_schema.sql`:

| Table | Purpose |
| --- | --- |
| `hosts` | One row per reporting hostname, with first/last seen times |
| `metrics` | Metric definitions `(name, unit)`, stored once |
| `metric_values` | Append-only samples: `host_id`, `metric_id`, `ts`, `value`, `tags` (JSONB) |

`metric_values` has no surrogate key. Its unique index on
`(host_id, metric_id, ts, tags)` is both the query index (host + metric +
time range) and a dedupe guard, so a batch the agent retries after a lost
response can be written with `ON CONFLICT DO NOTHING` without duplicating
data. That index already leads with `host_id`, so per-host sample queries
scale fine as hosts are added without any extra index.

`migrations/002_index_hosts_last_seen.sql` adds `hosts_last_seen_at_idx`
on `hosts (last_seen_at DESC)`, the one genuinely new access pattern
multi-host support introduces: listing hosts most-recently-active first
(`list_hosts()`, Week 6) and detecting stale/offline hosts by
`last_seen_at` (FLEET-04, later in Week 6).

## Alert rules

A rule reads as "`metric_name` `operator` `threshold` for
`duration_seconds`", e.g. `cpu.usage > 80` for 300 seconds. It is stored in
`alert_rules` (`migrations/003_alert_rules.sql`) and validated on the way in
by `AlertRuleIn` (`pulse_ingest/schemas.py`).

```json
{
  "name": "High overall CPU",
  "metric_name": "cpu.usage",
  "operator": ">",
  "threshold": 80,
  "duration_seconds": 300,
  "host": null,
  "tag_filter": {"core": null},
  "enabled": true
}
```

| Field | Meaning |
| --- | --- |
| `metric_name` | A metric name as the agent reports it. Not checked against `metrics`, so a rule can exist before any host reports that metric. |
| `operator` | One of `>`, `>=`, `<`, `<=`. No `==`: exact equality on sampled floats is almost never what a rule means. |
| `threshold` | Compared against the stored value in the metric's own unit (`percent` for `cpu.usage`, `GB` for `disk.free`). Must be finite. |
| `duration_seconds` | How long the condition must hold before the rule fires. `0` fires on the first breaching sample. 0-86400. |
| `host` | A hostname, or `null` for fleet-wide. Stored as `host_id`, so deleting a host deletes its own rules but not fleet-wide ones. |
| `tag_filter` | Narrows which series the rule watches (see below). Defaults to `{}`, every series of that metric. |

**Each series is evaluated on its own.** A rule watches every series
(host + tag set) that matches it, and each one can be OK or firing
independently. `cpu.usage > 80` fleet-wide with no filter therefore watches
every host's overall CPU *and* every one of its per-core series.

**`tag_filter`:** a string value requires that tag to equal it
(`{"mount": "/"}` watches only the root disk), and `null` requires the tag
to be **absent**. `{"core": null}` is how a rule targets overall CPU only.
That's the same distinction FLEET-03 and DASH-04 each got wrong once: the
overall reading is the one *without* a `core` tag, not the one with no tags
at all, since `environment` lands on every metric. An exact-match or
"contains" filter couldn't express it. A filter on `host` is rejected,
because the write path strips that tag before storing, so the filter could
never match. The `host` field does that job instead.

In SQL, a series' `tags` match a rule when
`tags @> jsonb_strip_nulls(tag_filter)` and `tags` has none of the keys
whose filter value is `null` (`tests/test_migrate.py` checks this
expression against Postgres).

The database repeats the important checks as constraints (operator set,
finite threshold, duration range, `tag_filter` is a JSON object), so a bad
row can't get in through some path other than the API.

## Rule evaluation

While the API is running, a background thread (`RuleEvaluator` in
`pulse_ingest/alerts.py`) evaluates every enabled rule every
`PULSE_ALERT_INTERVAL_SECONDS` (default 10, the agent's default reporting
interval). Each cycle:

1. One query (`rule_series()` in `store.py`) fetches every series each
   enabled rule matches (by the rule's `host` and `tag_filter`, as described
   above), with that series' **sliding window**: its samples from
   `duration_seconds` + 30s before its latest sample, up to that sample.
2. Each window is evaluated (`evaluate()` in `alerts.py`) into an
   `Evaluation` with three facts: `breaching` (the latest sample is past the
   threshold), `sustained` (it has stayed past it for the rule's full
   `duration_seconds`), and `breaching_since` (when the current run began).
3. The evaluations go to the alert state machine (`AlertTracker`, see
   [Alerts](#alerts-state-machine) below), which opens, fires, and resolves
   alerts in Postgres.

**Sustained duration (ALERT-03).** Walking back from the latest sample, a
series' current run is the unbroken stretch of samples that all breach. It
ends at a sample back within the threshold, or at a gap of more than 30s
between samples (`STALE_AFTER_SECONDS`, the same silence that marks a host
offline): with no data across the gap there's no evidence the breach held
through it. The breach is **sustained** once
`latest sample time - run start >= duration_seconds`. So with the agent's
10s interval, a 300s rule needs 31 breaching samples in a row, and a single
dip resets it. `duration_seconds = 0` is sustained on the first breaching
sample.

The window reaches 30s further back than the duration so that a run which
started right at the window's edge still shows up as one (any run long
enough to count has a sample in that extra 30s, since its samples are at
most 30s apart). The duration is measured in sample timestamps (the agent's
clock), not in evaluation cycles, so it doesn't depend on how often the
evaluator runs, and it survives an API restart because the history lives in
Postgres, not in memory.

One consequence: an agent configured with `interval_seconds` above 30
leaves gaps that break every run, so rules with a non-zero duration would
never be sustained. That's the same limit that would show such a host as
offline between reports.

**Only recent series count.** A series is evaluated only if its latest
sample is within the last 30s. Without that, a host that died mid-spike
would be judged on its last value forever. A series with no recent sample is
left out of the cycle entirely, so it is neither breaching nor OK.

Operational notes:

- If the database is unreachable, the cycle logs a one-line warning and the
  loop retries on the next interval. Any other error is logged with a
  traceback; neither stops the thread.
- The evaluator only starts when the app owns its Postgres store, i.e. a
  real `uvicorn` run, not API tests that inject a fake store.
- Run **one** API process. Each process runs its own evaluator, so
  `uvicorn --workers 4` would evaluate every rule four times. The unique
  index on open alerts stops duplicate alert rows, but two evaluators can
  still both load the same pending alert and both fire it, so the
  transition would be logged and notified twice.
- Shutdown interrupts the wait between cycles, so stopping the API doesn't
  hang for up to a full interval.

To try it, create a rule on the dashboard's **Alert rules** page (or with
`POST /rules`, below) and watch the API log and the `alerts` table.

## Rule endpoints (ALERT-05)

The dashboard's **Alert rules** page uses these. Bodies are the `AlertRuleIn`
shape from [Alert rules](#alert-rules) above; responses add `id`,
`created_at`, and `updated_at` (epoch seconds), with `host` as a hostname or
`null`.

| Method | Path | Result |
| --- | --- | --- |
| `GET` | `/rules` | `200 {"rules": [...]}`, oldest first |
| `GET` | `/rules/{id}` | `200` the rule, or `404` |
| `POST` | `/rules` | `201` the created rule |
| `PUT` | `/rules/{id}` | `200` the rule, replaced entirely (there's no partial update), or `404` |
| `DELETE` | `/rules/{id}` | `204`, or `404`. Alerts it raised are kept (see below). |

- Invalid bodies get FastAPI's usual `422` with field-level `detail`. A
  `host` that has never reported is a `422` too, reported at
  `["body", "host"]` like any other bad field, so a form can show it next to
  the host picker.
- A database outage is a `503`, as elsewhere.
- Changes are picked up on the evaluator's next cycle; nothing needs
  restarting.
- **No authentication**, like the other dashboard endpoints: the dashboard
  has no credentials of its own to send. That means anyone who can reach
  the API can change the rules, so the API (port 8000) must stay on a
  trusted network and not be exposed publicly. Real user auth is out of
  scope for this semester's plan.

```bash
curl -X POST http://127.0.0.1:8000/rules -H "Content-Type: application/json" \
  -d '{"name": "High overall CPU", "metric_name": "cpu.usage", "operator": ">",
       "threshold": 90, "duration_seconds": 300, "tag_filter": {"core": null}}'
```

## Alerts (state machine)

Each (rule, series) pair moves through **OK → Pending → Firing →
Resolved**, stored in the `alerts` table (`migrations/004_alerts.sql`) by
`AlertTracker` in `pulse_ingest/alerts.py`. The decisions themselves are a
pure function, `plan_transitions(open_alerts, evaluations, now)`, so every
transition is unit-tested without a database.

| Open alert | This cycle's evaluation | Result |
| --- | --- | --- |
| none | breaching, not sustained | new alert, **pending** |
| none | sustained (e.g. `duration_seconds` = 0) | new alert, straight to **firing** |
| pending | still breaching | stays pending; `pending_since` follows the current run |
| pending | sustained | **firing**, `fired_at` = now |
| pending | not breaching, or not evaluated | **deleted** (cancelled before firing) |
| firing | still breaching (sustained or not) | stays firing |
| firing | not breaching | **resolved**, `resolve_reason = 'recovered'` |
| firing | not evaluated | **resolved**, `resolve_reason = 'not_evaluated'` |

The API log shows each transition:

```
INFO    Alert 12 pending, fires if sustained for 300s: #1 'High CPU' on web-1: cpu.usage{core=0} = 93.5 percent (> 80)
WARNING Alert 12 FIRING: #1 'High CPU' on web-1: cpu.usage{core=0} = 95 percent (> 80)
INFO    Alert 12 resolved (recovered) after 420s firing: #1 'High CPU' on web-1: cpu.usage{core=0} = 41 percent (> 80)
```

Design decisions:

- **State lives in Postgres, not memory.** An API restart picks up the open
  alerts where they were instead of re-firing everything (which would mean
  duplicate notifications). A partial unique index
  allows at most one open alert per rule and series, and each cycle's
  changes are written in one transaction.
- **A restart doesn't count as the hosts going quiet.** While the API is
  down (or can't reach the database), agents keep collecting and retry with
  backoff of up to 60s, so for a while after it comes back every series'
  latest sample can be over 30s old. Without a guard, the first cycle would
  resolve each firing alert as `not_evaluated`, emailing "this is NOT a
  recovery" for every one, and fire a fresh alert seconds later when the
  buffered data landed. So for `CATCH_UP_SECONDS` (90s: the agents' longest
  backoff, plus a reporting interval, plus margin) after the evaluator
  starts or the database comes back, an **enabled** rule's alert that
  isn't evaluated is held as it is. Alerts of a disabled or deleted rule
  close immediately as usual, since that's an answer, not a data gap. The
  cost: a host that really did die while the API was down is reported up
  to 90s later than it otherwise would be. Found running the MVP against a
  test fleet (OPS-03), where a 20s API restart did exactly this.
- **A pending alert that recovers is deleted, not resolved.** It never
  fired, so it isn't history. Every resolved row is an alert that actually
  fired, which is what the alert history page (DASH-09) lists.
- **Once firing, it stays firing while the latest sample breaches,** even if
  the run restarted after a dip between cycles. `duration_seconds` only
  gates pending → firing (the same as Prometheus's `for:`), so an alert
  can't flap between resolved and firing on a noisy series.
- **"Not evaluated" is not "recovered."** If a firing series disappears
  from the evaluation (the host stopped reporting, or the rule was disabled,
  edited to no longer match, or deleted), the alert resolves with
  `not_evaluated`. Claiming the CPU recovered when the host simply went
  dark would be wrong; the reason lets a notification say which it was.
- **Deleting a rule keeps its alerts.** `alerts.rule_id` is
  `ON DELETE SET NULL`, and each alert keeps a copy of the rule's name and
  condition, refreshed on every cycle while it's open, so the history reads
  correctly after the rule is edited or deleted. Open alerts whose rule was
  deleted resolve (or cancel) on the next cycle. Deleting a host deletes its
  alerts along with its samples.
- **Times:** `pending_since` is the start of the breaching run (sample
  time, the agent's clock); `fired_at` and `resolved_at` are when the
  evaluator decided (the API server's clock).
- **A new rule judges existing history.** The sliding window reads stored
  samples, so if the condition has already held for `duration_seconds`
  when a rule is created (or enabled), it fires on its first cycle with no
  pending step. `memory.used > 1 for 4s` on a host that has reported for a
  minute fires within a second of being saved. That's the condition being
  true, not a shortcut.

## Notifications (NOTIFY-01, NOTIFY-02)

When an alert **fires** or **resolves**, the API emails it, posts it to
Slack, or both (`pulse_ingest/notify.py`). Pending and cancelled alerts
send nothing, since they never fired. Each channel is configured on its
own; with neither, notifications are off.

### Email

Email is off unless configured with these environment variables:

| Variable | Meaning |
| --- | --- |
| `PULSE_SMTP_HOST` | SMTP server, e.g. `smtp.gmail.com` (required) |
| `PULSE_ALERT_EMAIL_FROM` | Sender, e.g. `Pulse <alerts@example.com>` (required) |
| `PULSE_ALERT_EMAIL_TO` | Recipients, comma-separated (required) |
| `PULSE_SMTP_SECURITY` | `starttls` (default), `ssl`, or `none` |
| `PULSE_SMTP_PORT` | Defaults to 587 / 465 / 25 to match the security mode |
| `PULSE_SMTP_USERNAME`, `PULSE_SMTP_PASSWORD` | Login, if the server needs one (both or neither) |

Setting any of them without all three required ones stops the API at
startup with a message naming what's missing, rather than running with
alerts that silently go nowhere. The startup log says whether email is on, and to whom.
For Gmail, use `smtp.gmail.com` with an
[app password](https://support.google.com/accounts/answer/185833), not
the account password.

To try it locally without a real mail account, run a console mail server
that prints every message it receives (`aiosmtpd` comes with the dev
dependencies):

```bash
python -m aiosmtpd -n -l 127.0.0.1:1025      # in one terminal
PULSE_SMTP_HOST=127.0.0.1 PULSE_SMTP_PORT=1025 PULSE_SMTP_SECURITY=none \
PULSE_ALERT_EMAIL_FROM=pulse@localhost PULSE_ALERT_EMAIL_TO=me@localhost \
uvicorn pulse_ingest.app:app                  # in another
```

A firing email looks like this:

```
Subject: [Pulse] FIRING: Root disk on web-1 (mount=/)

Root disk is FIRING on web-1.

  Series:          disk.usage{mount=/}
  Value:           93.1 percent
  Condition:       disk.usage > 90 for 5m
  Breaching since: 2026-10-19 21:15:02 UTC
  Fired at:        2026-10-19 21:20:04 UTC

Alert #2, rule #4.
```

The resolve email adds `Resolved at` and `Firing for`. Its subject depends
on why the alert closed:

- `[Pulse] RESOLVED: ...` when the value came back within the threshold
  (`recovered`).
- `[Pulse] RESOLVED (no longer evaluated): ...` when Pulse stopped
  evaluating it (`not_evaluated`). The email says plainly that this is
  **not** a recovery: the host may have stopped reporting, which can be
  worse than the original alert. Disabling or deleting the rule, or
  editing it so it no longer matches the series, closes its alerts the
  same way.

### Slack

Set `PULSE_SLACK_WEBHOOK_URL` to a Slack
[incoming webhook](https://api.slack.com/messaging/webhooks) URL and every
fire and resolve is posted to that webhook's channel. To get one: at
<https://api.slack.com/apps>, **Create New App** (from scratch), turn on
**Incoming Webhooks**, click **Add New Webhook to Workspace**, pick the
channel, and copy the `https://hooks.slack.com/services/...` URL. To try
it, use a test channel in a workspace you own (a free one works) and create
a rule that is always true, like `memory.total > 0`.

Each message is the email's subject line, then the email's details in an
attachment whose color bar shows what happened: red for firing, green for
recovered, amber for no longer evaluated (with the same "NOT because the
value recovered" explanation as the email). In Slack it reads like this:

```
[Pulse] FIRING: Root disk on web-1 (mount=/)
┃ Root disk is FIRING on web-1.
┃ Series                     Value
┃ disk.usage{mount=/}        93.1 percent
┃ Condition                  Breaching since
┃ disk.usage > 90 for 5m     2026-10-19 21:15:02 UTC
┃ Fired at
┃ 2026-10-19 21:20:04 UTC
┃ Alert #2, rule #4.
```

- **The URL is the secret.** Anyone who has it can post to the channel,
  so treat it like a password. Pulse never logs it: the startup log shows
  only its host, and the errors a failed post logs (Slack's reason, like
  `no_service` for a deleted webhook) don't include it. An `http://` URL
  logs a warning, since it would cross the network unencrypted, and
  anything that isn't an `http(s)://` URL with a host stops the API at
  startup.
- **Names can't ping anyone.** Rule names, hostnames, and tags are
  escaped (`&`, `<`, `>`), so a rule named `<!channel>` shows up as text
  instead of notifying the whole channel, and nothing in them can turn
  into a link.
- **Long values are cut.** Slack rejects the whole message if one field is
  over 2,000 characters, which a series with many long tags can reach, so
  such a field is cut short with `…` instead.

### Delivery

- **Off the evaluation thread.** `AlertTracker` hands each change to a
  `Notifier` only after the change is committed, and the `Notifier` sends
  from one thread per channel. A slow or unreachable mail server or webhook
  never delays rule evaluation or the other channel (Slack messages don't
  wait while email retries), and nothing is announced that didn't make it
  into the database.
- **Retried, then logged.** A failed send is retried after 5s and again
  after 30s, then logged at ERROR as given up. Each successful send is
  logged at INFO.
- **Best effort, in memory.** Queued notifications are lost if the process
  dies before sending them, and each channel's queue drops new ones past
  1,000 (logged at ERROR). On a normal shutdown, the API sends what's
  queued first, for up to 10s.
- One email (to every recipient) and one Slack message per transition.
  There are no per-rule recipients or channels, and no batching.

## Alert history (DASH-09)

**`GET /alerts`** lists the alerts that fired, for the dashboard's
**Alerts** page. No API key, like the other dashboard endpoints.

| Query param | Meaning |
| --- | --- |
| `host` | Only this host's alerts. An unknown host just matches nothing (`200`, empty). |
| `state` | `firing` or `resolved`; omitted means both. Anything else is a `422`. |
| `limit` | How many to return, 1-1000 (default 100). |

```json
{
  "alerts": [
    {"id": 2, "rule_id": 4, "rule_name": "Root disk", "host": "web-1",
     "metric_name": "disk.usage", "unit": "percent", "tags": {"mount": "/"},
     "operator": ">", "threshold": 90.0, "duration_seconds": 300,
     "state": "resolved", "pending_since": 1792890902.0, "fired_at": 1792891204.0,
     "resolved_at": 1792891629.0, "resolve_reason": "recovered",
     "last_value": 71.5, "last_evaluated_at": 1792891629.0}
  ],
  "has_more": false,
  "firing_count": 1
}
```

- **Only alerts that fired.** Pending alerts haven't, and one that recovers
  before firing is deleted (see [Alerts](#alerts-state-machine)), so every
  entry is `firing` or `resolved`.
- **Still-firing first,** then newest `fired_at` first. Alerts that fire in
  the same evaluation cycle share `fired_at` exactly, so `id` breaks the tie
  and the order never shuffles between requests.
- **`has_more`** says more alerts match than `limit` let through (the query
  fetches one extra row to find out). The dashboard's "Show more" asks again
  with a bigger `limit` rather than paging with an offset, so alerts that
  fire in the meantime can't shift a page and show something twice.
- **`firing_count`** is how many alerts are firing right now on the
  requested host (or fleet-wide without `host`), ignoring `state` and
  `limit`. It's the metrics catalog's "Alerts currently firing" number, and
  it lets a page filtered to resolved alerts still say what's on fire.
- `rule_name`, the condition, and `tags` are the alert's own copies, so
  entries still read correctly after the rule is edited or deleted
  (`rule_id` is then `null`).
- Duration isn't a field: it's `resolved_at - fired_at`, or now minus
  `fired_at` while still firing, which the dashboard computes so it keeps
  ticking between polls.

## Smoke test (OPS-02)

One command that checks the whole pipeline end to end, for before a demo or
after changing anything that touches it:

```bash
scripts/smoke-test.sh            # from the repo root
```

It starts the real API and the real agent as separate processes against a
scratch database, with local stand-ins for the mail server and the Slack
webhook, and drives them only the way a deployment would: through the
agent's config file, the REST API, and real SMTP and webhook deliveries.
Each check prints a line; the exit code is 0 if all pass, 1 if one fails
(it stops there and prints the tail of the API and agent logs), and 2 if the
agent isn't installed.

```
Pulse smoke test: agent -> API -> Postgres -> alert -> email + Slack
  ok      0.1s  scratch database pulse_smoke_9e7a658e created, 4 migrations applied
  ok      0.0s  local SMTP server on :34255, Slack webhook stand-in on :38113
  ok      0.3s  API healthy on :36043 (email and Slack notifications on)
  ok      0.3s  agent reporting as smoke-host: online, 19 series stored
  ok      0.0s  rule #1 created through POST /rules: memory.total > 0 for 3s on smoke-host
  ok      3.3s  alert #1 firing 3.3s later, listed by GET /alerts
  ok      0.0s  firing email and Slack message delivered: '[Pulse] FIRING: Smoke test on smoke-host (environment=smoke)'
  ok      0.5s  threshold raised through PUT /rules; alert #1 resolved (recovered) after 0.5s
  ok      0.0s  resolved email and Slack message delivered: '[Pulse] RESOLVED: Smoke test on smoke-host (environment=smoke)'
  ok      2.0s  exactly one email and one Slack message per transition
  ok      0.0s  rule deleted; alert #1 stays in the history
PASS: 11 checks in 6.9s
```

How it differs from `tests/test_round_trip.py`, which covers similar
ground inside pytest:

- **Only public interfaces.** The rule is created, edited, and deleted with
  `POST`/`PUT`/`DELETE /rules`, and every result is read back through
  `GET /hosts` and `GET /alerts`, never with SQL. If the dashboard could
  see it, the smoke test checks it the same way.
- **A real recovery.** It resolves the alert by raising the rule's threshold
  out of reach, so the value is genuinely back within it (`recovered`). The
  round-trip test resolves by disabling the rule (`not_evaluated`), so
  between them both paths get exercised end to end.
- **Exactly once.** After the resolve it waits a few more evaluation cycles
  and checks that exactly two emails and two Slack messages went out: none
  for the pending step, and no duplicates.
- **History survives.** Deleting the rule leaves the alert listed by
  `GET /alerts` with `rule_id: null`.

It never touches a real inbox, channel, or database: `PULSE_*` settings
from your shell are ignored, the API gets its own random key, and the
scratch database is dropped afterwards even when a check fails.
`--timeout SECONDS` (default 30) sets how long each step may take, and
`--agent PATH` runs a different `pulse-agent`. `tests/test_smoke.py` runs
it as part of the test suite, including that a failure names the check,
prints the logs, and still cleans up.

## Migrations

`python -m pulse_ingest.migrate` applies each un-applied `migrations/*.sql`
file in filename order. Each runs in its own transaction with its
`schema_migrations` bookkeeping row, so a failing migration leaves nothing
half-applied. To add one, create the next numbered file, e.g.
`002_add_something.sql`.

## Task mapping (Week 4)

| Task | File(s) |
| --- | --- |
| INGEST-01 payload schema | `pulse_ingest/schemas.py`, `tests/test_schemas.py` |
| INGEST-02 API scaffold + health check | `pulse_ingest/app.py`, `tests/test_health.py` |
| INGEST-03 `POST /metrics` + validation | `pulse_ingest/app.py`, `tests/test_ingest.py` |
| INGEST-04 API-key auth middleware | `pulse_ingest/auth.py`, `pulse_ingest/app.py`, `tests/test_auth.py` |
| STORE-03 batched write path | `pulse_ingest/store.py`, `pulse_ingest/app.py`, `tests/test_store.py` |
| INGEST-05 agent → API → Postgres round trip | `tests/test_round_trip.py` |
| STORE-01 Postgres schema | `migrations/001_initial_schema.sql` |
| STORE-02 migrations | `pulse_ingest/migrate.py`, `tests/test_migrate.py`, `scripts/dev-db.sh` |

## Task mapping (Week 5)

| Task | File(s) |
| --- | --- |
| DASH-02 latest-values endpoint | `pulse_ingest/store.py` (`get_latest`), `pulse_ingest/app.py`, `tests/test_queries.py`, `tests/test_dashboard_endpoints.py` |
| DASH-03 historical-values endpoint | `pulse_ingest/store.py` (`get_history`), `pulse_ingest/app.py`, `tests/test_queries.py`, `tests/test_dashboard_endpoints.py` |

## Task mapping (Week 6)

| Task | File(s) |
| --- | --- |
| FLEET-01 index/query cleanly across many hosts | `migrations/002_index_hosts_last_seen.sql`, `pulse_ingest/store.py` (`list_hosts`), `tests/test_queries.py`, `tests/test_migrate.py` |
| FLEET-02 host self-registration | `pulse_ingest/store.py` (`WriteResult.new_host`), `pulse_ingest/app.py`, `tests/test_store.py`, `tests/test_ingest.py`, `tests/fakes.py` |
| FLEET-03 fleet overview endpoint | `pulse_ingest/store.py` (`list_hosts` key metrics), `pulse_ingest/app.py` (`GET /hosts`), `tests/test_queries.py`, `tests/test_dashboard_endpoints.py` |
| FLEET-04 heartbeat/stale detection | `pulse_ingest/store.py` (`STALE_AFTER_SECONDS`, `HostSummary.status`), `tests/test_queries.py`, `tests/test_dashboard_endpoints.py` |
| FLEET-05 per-host detail endpoint | `pulse_ingest/store.py` (`get_host_summary`), `pulse_ingest/app.py` (`GET /hosts/{host}`), `tests/test_queries.py`, `tests/test_dashboard_endpoints.py` |

## Task mapping (Week 7)

| Task | File(s) |
| --- | --- |
| ALERT-01 alert rule schema | `migrations/003_alert_rules.sql`, `pulse_ingest/schemas.py` (`AlertRuleIn`), `tests/test_schemas.py`, `tests/test_migrate.py` |
| ALERT-02 rule evaluation loop | `pulse_ingest/alerts.py`, `pulse_ingest/store.py` (`rule_series`), `pulse_ingest/app.py` (lifespan), `tests/test_alerts.py`, `tests/test_alert_queries.py`, `tests/test_round_trip.py` |
| ALERT-03 sustained duration via sliding window | `pulse_ingest/alerts.py` (`evaluate`), `pulse_ingest/store.py` (`rule_series` window), `tests/test_alerts.py`, `tests/test_alert_queries.py`, `tests/test_round_trip.py` |
| ALERT-04 alert state machine | `migrations/004_alerts.sql`, `pulse_ingest/alerts.py` (`plan_transitions`, `AlertTracker`), `pulse_ingest/store.py` (`open_alerts`, `apply_alert_changes`), `tests/test_alerts.py`, `tests/test_alert_store.py`, `tests/test_migrate.py`, `tests/test_round_trip.py` |
| ALERT-05 rule management (API side) | `pulse_ingest/app.py` (`/rules` endpoints), `pulse_ingest/store.py` (`list_rules` .. `delete_rule`, `UnknownHostError`), `tests/test_rule_endpoints.py`, `tests/test_rule_store.py`, `tests/fakes.py`; UI in `dashboard/` |
| NOTIFY-01 email on fire and resolve | `pulse_ingest/notify.py`, `pulse_ingest/alerts.py` (`AlertTracker` hook), `pulse_ingest/app.py` (lifespan), `tests/test_notify.py`, `tests/test_alerts.py`, `tests/test_round_trip.py`, `tests/conftest.py` (`smtp_sink`) |

## Task mapping (Week 8)

| Task | File(s) |
| --- | --- |
| NOTIFY-02 Slack on fire and resolve | `pulse_ingest/notify.py` (`SlackConfig`, `build_slack_message`, `SlackChannel`, one `Notifier` thread per channel), `pulse_ingest/app.py` (`_notifier`), `tests/test_notify.py`, `tests/test_round_trip.py`, `tests/conftest.py` (`webhook_sink`) |
| DASH-09 alert history (API side) | `pulse_ingest/store.py` (`list_alerts`, `AlertHistory`), `pulse_ingest/app.py` (`GET /alerts`), `tests/test_alert_store.py`, `tests/test_alert_endpoints.py`, `tests/fakes.py`; page in `dashboard/` |
| OPS-02 end-to-end smoke test | `pulse_ingest/smoke.py` (also hosts the SMTP/webhook stand-ins the tests use), `scripts/smoke-test.sh`, `tests/test_smoke.py`, `tests/conftest.py` |
| OPS-03 restart catch-up (found against a test fleet) | `pulse_ingest/alerts.py` (`CATCH_UP_SECONDS`, `RuleEvaluator` catching-up flag, `plan_transitions(hold_rule_ids=)`, `AlertTracker`), `pulse_ingest/store.py` (`enabled_rule_ids`), `tests/test_alerts.py`, `tests/test_alert_store.py`; the rest of OPS-03 is in `agent/`, `scripts/test-fleet.sh`, and `docs/ops-03-stability-run.md` |
