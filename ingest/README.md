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
| STORE-01 Postgres schema | `migrations/001_initial_schema.sql` |
| STORE-02 migrations | `pulse_ingest/migrate.py`, `tests/test_migrate.py`, `scripts/dev-db.sh` |
