-- Hosts that report into Pulse. One row per agent hostname.
CREATE TABLE hosts (
    id            BIGSERIAL PRIMARY KEY,
    hostname      TEXT        NOT NULL UNIQUE,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Metric definitions, e.g. ('cpu.usage', 'percent'). Kept separate from the
-- values so the name/unit strings aren't repeated on every sample.
CREATE TABLE metrics (
    id   BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    unit TEXT NOT NULL,
    UNIQUE (name, unit)
);

-- Append-only time series samples. `tags` holds the per-sample dimensions
-- (core, mount, device, interface, environment, ...), so one metric can fan
-- out into many series without a schema change.
CREATE TABLE metric_values (
    host_id   BIGINT           NOT NULL REFERENCES hosts (id) ON DELETE CASCADE,
    metric_id BIGINT           NOT NULL REFERENCES metrics (id),
    ts        TIMESTAMPTZ      NOT NULL,
    value     DOUBLE PRECISION NOT NULL,
    tags      JSONB            NOT NULL DEFAULT '{}'::jsonb
);

-- Natural key of a sample. Doubles as the query index (host, metric, time
-- range) and makes ingestion idempotent: if an agent retries a batch the
-- server already committed, the write path can use ON CONFLICT DO NOTHING.
CREATE UNIQUE INDEX metric_values_natural_key
    ON metric_values (host_id, metric_id, ts, tags);
