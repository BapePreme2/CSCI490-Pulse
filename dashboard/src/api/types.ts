export interface LatestMetric {
  name: string;
  unit: string;
  tags: Record<string, string>;
  value: number;
  timestamp: number;
}

export interface LatestMetricsResponse {
  host: string;
  metrics: LatestMetric[];
}

export interface HistoryPoint {
  timestamp: number;
  value: number;
}

export interface HistorySeries {
  unit: string;
  tags: Record<string, string>;
  points: HistoryPoint[];
}

export interface HistoryResponse {
  host: string;
  name: string;
  start: number;
  end: number;
  series: HistorySeries[];
}

export type HostStatus = "online" | "offline";

export interface FleetHost {
  hostname: string;
  first_seen_at: number;
  last_seen_at: number;
  status: HostStatus;
  cpu_usage: number | null;
  memory_percent: number | null;
}

export interface FleetHostsResponse {
  hosts: FleetHost[];
}

/** Same shape as one entry of GET /hosts; the semantic name for the
 * single-host GET /hosts/{host} response. */
export type HostSummary = FleetHost;

export type Operator = ">" | ">=" | "<" | "<=";

/** "threshold" compares each sample's value with `threshold`. "anomaly"
 * compares how many standard deviations it is from the series' baseline over
 * the trailing `baseline_seconds`: `>` 3 is "more than 3σ above", `<` -3
 * "more than 3σ below". */
export type RuleKind = "threshold" | "anomaly";

/** What the API accepts to create or replace a rule (`AlertRuleIn`). */
export interface AlertRuleInput {
  name: string;
  metric_name: string;
  operator: Operator;
  threshold: number;
  duration_seconds: number;
  /** null = fleet-wide. */
  host: string | null;
  /** A string value requires that tag value; null requires the tag to be absent. */
  tag_filter: Record<string, string | null>;
  enabled: boolean;
  kind: RuleKind;
  /** Anomaly rules only; null for threshold rules. */
  baseline_seconds: number | null;
}

export interface AlertRule extends AlertRuleInput {
  id: number;
  created_at: number;
  updated_at: number;
}

export interface AlertRulesResponse {
  rules: AlertRule[];
}

/** Only alerts that fired are history; pending ones aren't returned. */
export type AlertState = "firing" | "resolved";

/** "recovered": back within the threshold. "not_evaluated": Pulse stopped
 * evaluating it (host went quiet, or the rule was disabled/changed/deleted). */
export type ResolveReason = "recovered" | "not_evaluated";

/** One entry of GET /alerts. The rule's name and condition are as of the
 * alert's last evaluation, so they still read right after the rule changes. */
export interface Alert {
  id: number;
  /** null once the rule has been deleted. */
  rule_id: number | null;
  rule_name: string;
  host: string;
  metric_name: string;
  unit: string;
  tags: Record<string, string>;
  operator: Operator;
  threshold: number;
  duration_seconds: number;
  state: AlertState;
  pending_since: number;
  fired_at: number;
  resolved_at: number | null;
  resolve_reason: ResolveReason | null;
  last_value: number;
  last_evaluated_at: number;
  kind: RuleKind;
  baseline_seconds: number | null;
  /** Anomaly alerts: the baseline the last evaluation compared against, the
   * stddev being the spread the deviation was measured in. */
  last_baseline_mean: number | null;
  last_baseline_stddev: number | null;
}

export interface AlertHistoryResponse {
  /** Still-firing first, then newest first. */
  alerts: Alert[];
  /** More alerts match than the requested limit. */
  has_more: boolean;
  /** Alerts firing now on the requested host(s), regardless of the state filter. */
  firing_count: number;
}

export type AnomalyDirection = "above" | "below";

/** One sample at least `sigma` from its rolling baseline. `spread` is the
 * standard deviation the deviation was measured in (at least 1% of the
 * mean). `deviation` is null against a baseline that was constantly 0: any
 * change from it is infinitely far off, in the run's direction. */
export interface AnomalyPoint {
  timestamp: number;
  value: number;
  mean: number;
  spread: number;
  deviation: number | null;
}

/** A run of one series' consecutive samples, all anomalous in one direction. */
export interface Anomaly {
  start: number;
  end: number;
  direction: AnomalyDirection;
  points: AnomalyPoint[];
}

export interface AnomalySeries {
  unit: string;
  tags: Record<string, string>;
  anomalies: Anomaly[];
}

/** GET /hosts/{host}/metrics/{name}/anomalies. Only series with at least one
 * anomaly are listed. */
export interface AnomaliesResponse {
  host: string;
  name: string;
  start: number;
  end: number;
  window_seconds: number;
  min_samples: number;
  sigma: number;
  series: AnomalySeries[];
}
