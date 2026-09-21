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

- `202 {"accepted": N}` when the whole batch is valid.
- `422` with FastAPI's field-level `detail` (e.g. `["body", "metrics", 1,
  "value"]` points at the bad metric) when any part of it is invalid. A batch
  is all-or-nothing: one bad metric rejects the request.

Until STORE-03 lands the endpoint validates and counts the batch but does
not persist it.

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
data.

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
| STORE-01 Postgres schema | `migrations/001_initial_schema.sql` |
| STORE-02 migrations | `pulse_ingest/migrate.py`, `tests/test_migrate.py`, `scripts/dev-db.sh` |
