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
