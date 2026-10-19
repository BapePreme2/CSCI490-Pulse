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
