-- Alert rules: "<metric_name> <operator> <threshold> for <duration_seconds>",
-- e.g. cpu.usage > 80 for 300s. Each matching series (host + tag set) is
-- evaluated on its own, so a rule can fire for one host or core and not
-- another.
CREATE TABLE alert_rules (
    id               BIGSERIAL PRIMARY KEY,
    name             TEXT             NOT NULL CHECK (name <> ''),
    -- A name, not a metrics.id: a rule can be created before any host has
    -- reported that metric, and metrics rows only appear on first ingest.
    metric_name      TEXT             NOT NULL CHECK (metric_name <> ''),
    operator         TEXT             NOT NULL CHECK (operator IN ('>', '>=', '<', '<=')),
    -- Postgres sorts NaN above Infinity, so this also rejects NaN.
    threshold        DOUBLE PRECISION NOT NULL
                     CHECK (threshold > '-Infinity' AND threshold < 'Infinity'),
    -- How long the condition must hold before the rule fires; 0 fires on the
    -- first breaching sample. Capped at a day.
    duration_seconds INTEGER          NOT NULL CHECK (duration_seconds BETWEEN 0 AND 86400),
    -- NULL means fleet-wide (every host).
    host_id          BIGINT           REFERENCES hosts (id) ON DELETE CASCADE,
    -- Narrows which series the rule watches. {"mount": "/"} requires that tag
    -- value; {"core": null} requires the tag to be absent, which is how a rule
    -- targets overall CPU rather than every per-core series too.
    tag_filter       JSONB            NOT NULL DEFAULT '{}'::jsonb
                     CHECK (jsonb_typeof(tag_filter) = 'object'),
    enabled          BOOLEAN          NOT NULL DEFAULT true,
    created_at       TIMESTAMPTZ      NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ      NOT NULL DEFAULT now()
);
