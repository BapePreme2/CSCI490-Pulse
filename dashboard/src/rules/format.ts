import type { AlertRule } from "../api/types";

/** 300 -> "5m", 90 -> "1m 30s", 5400 -> "1h 30m", 45 -> "45s". */
export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = seconds % 60;
  const parts = [hours && `${hours}h`, minutes && `${minutes}m`, secs && `${secs}s`].filter(Boolean);
  return parts.join(" ");
}

/** "cpu.usage > 80 for 5m", or without "for ..." when it fires on the first sample. */
export function describeCondition(rule: Pick<AlertRule, "metric_name" | "operator" | "threshold" | "duration_seconds">): string {
  const condition = `${rule.metric_name} ${rule.operator} ${rule.threshold}`;
  return rule.duration_seconds > 0 ? `${condition} for ${formatDuration(rule.duration_seconds)}` : condition;
}

/** {"core": null, "mount": "/"} -> "no core tag, mount = /"; {} -> "". */
export function describeTagFilter(filter: Record<string, string | null>): string {
  return Object.entries(filter)
    .map(([key, value]) => (value === null ? `no ${key} tag` : `${key} = ${value}`))
    .join(", ");
}
