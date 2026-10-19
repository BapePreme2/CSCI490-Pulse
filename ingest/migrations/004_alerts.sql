-- Alerts: one row per time a rule's condition held on one series (rule +
-- host + unit + tags), moving pending -> firing -> resolved. A pending alert
-- that recovers before firing is deleted rather than resolved, so resolved
-- rows are exactly the alerts that fired: the alert history.
CREATE TABLE alerts (
    id                BIGSERIAL PRIMARY KEY,
    -- SET NULL, not CASCADE: deleting a rule keeps the alerts it raised.
    rule_id           BIGINT           REFERENCES alert_rules (id) ON DELETE SET NULL,
    host_id           BIGINT           NOT NULL REFERENCES hosts (id) ON DELETE CASCADE,
    unit              TEXT             NOT NULL,
    tags              JSONB            NOT NULL DEFAULT '{}'::jsonb,
    -- The rule's condition as of the alert's last evaluation, so the history
    -- still reads correctly after the rule is edited or deleted.
    rule_name         TEXT             NOT NULL,
    metric_name       TEXT             NOT NULL,
    operator          TEXT             NOT NULL,
    threshold         DOUBLE PRECISION NOT NULL,
    duration_seconds  INTEGER          NOT NULL,
    state             TEXT             NOT NULL CHECK (state IN ('pending', 'firing', 'resolved')),
    -- When the breaching run behind this alert began.
    pending_since     TIMESTAMPTZ      NOT NULL,
    fired_at          TIMESTAMPTZ,
    resolved_at       TIMESTAMPTZ,
    -- 'recovered': the value went back within the threshold.
    -- 'not_evaluated': the series stopped reporting, or the rule was
    -- disabled, changed, or deleted, so whether it recovered is unknown.
    resolve_reason    TEXT             CHECK (resolve_reason IN ('recovered', 'not_evaluated')),
    last_value        DOUBLE PRECISION NOT NULL,
    last_evaluated_at TIMESTAMPTZ      NOT NULL,
    CHECK (
        (state = 'pending' AND fired_at IS NULL AND resolved_at IS NULL AND resolve_reason IS NULL)
     OR (state = 'firing' AND fired_at IS NOT NULL AND resolved_at IS NULL AND resolve_reason IS NULL)
     OR (state = 'resolved' AND fired_at IS NOT NULL AND resolved_at IS NOT NULL AND resolve_reason IS NOT NULL)
    )
);

-- At most one open (pending or firing) alert per rule and series, which is
-- also the set the evaluator loads every cycle.
CREATE UNIQUE INDEX alerts_one_open_per_series
    ON alerts (rule_id, host_id, unit, tags) WHERE state <> 'resolved';
