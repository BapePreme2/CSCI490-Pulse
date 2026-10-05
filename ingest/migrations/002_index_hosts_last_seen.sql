-- Supports fleet-wide queries: listing hosts ordered by recent activity
-- (FLEET-03) and detecting stale/offline hosts by last_seen_at (FLEET-04).
-- The unique index on metric_values already leads with host_id, so
-- per-host sample queries scale fine as hosts are added; this is the one
-- genuinely new access pattern multi-host support introduces.
CREATE INDEX hosts_last_seen_at_idx ON hosts (last_seen_at DESC);
