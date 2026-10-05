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
- a wrong API key stores nothing while the agent keeps retrying.

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

**`GET /hosts`** -- every known host for the fleet overview page, with two
"key metrics" computed in a single query (not one per host): the overall
(untagged) `cpu.usage` and a `memory_percent` derived from
`memory.used`/`memory.total`. Either is `null` if that host has no such
data yet. This is identity/status only -- it does not say whether a host
is online or offline; that determination is FLEET-04.

```json
{
  "hosts": [
    {"hostname": "web-1", "first_seen_at": 1789415000.0, "last_seen_at": 1789419042.0,
     "cpu_usage": 42.5, "memory_percent": 61.2}
  ]
}
```

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
