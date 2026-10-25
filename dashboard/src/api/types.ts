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
}

export interface AlertHistoryResponse {
  /** Still-firing first, then newest first. */
  alerts: Alert[];
  /** More alerts match than the requested limit. */
  has_more: boolean;
  /** Alerts firing now on the requested host(s), regardless of the state filter. */
  firing_count: number;
}
