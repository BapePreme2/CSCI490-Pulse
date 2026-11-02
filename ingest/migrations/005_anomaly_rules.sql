-- Anomaly rules (ANOM-02): instead of comparing a series' raw value with a
-- fixed threshold, compare how many standard deviations each sample is from
-- the series' own rolling baseline (mean and std-dev over the trailing
-- baseline_seconds; see pulse_ingest/anomaly.py). "cpu.usage > baseline +
-- 3 sigma" is stored as kind 'anomaly', operator '>', threshold 3, and "more
-- than 3 sigma below" as operator '<', threshold -3, so the evaluator applies
-- the same operator, threshold, and sustained-duration logic to the
-- deviation that it applies to a threshold rule's value.
ALTER TABLE alert_rules
    ADD COLUMN kind TEXT NOT NULL DEFAULT 'threshold' CHECK (kind IN ('threshold', 'anomaly')),
    ADD COLUMN baseline_seconds INTEGER CHECK (baseline_seconds BETWEEN 60 AND 86400),
    ADD CHECK (
        (kind = 'threshold' AND baseline_seconds IS NULL)
     OR (kind = 'anomaly' AND baseline_seconds IS NOT NULL
         -- "above" needs a positive number of sigma, "below" a negative one:
         -- "> baseline - 2 sigma" would be true nearly all the time.
         AND ((operator IN ('>', '>=') AND threshold > 0) OR (operator IN ('<', '<=') AND threshold < 0)))
    );

-- Alerts keep a copy of the rule's condition, so they need its kind and
-- baseline window too, plus the baseline the last evaluation compared
-- against (NULL for threshold alerts). last_baseline_stddev is the spread
-- the deviation was measured in, after the floor anomaly.spread() applies.
ALTER TABLE alerts
    ADD COLUMN kind TEXT NOT NULL DEFAULT 'threshold' CHECK (kind IN ('threshold', 'anomaly')),
    ADD COLUMN baseline_seconds INTEGER,
    ADD COLUMN last_baseline_mean DOUBLE PRECISION,
    ADD COLUMN last_baseline_stddev DOUBLE PRECISION;
