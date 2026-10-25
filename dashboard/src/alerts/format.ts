import type { Alert } from "../api/types";

/** What happened to an alert, as the API's notifications tell it apart:
 * a "not evaluated" resolve is not a recovery. */
export type AlertOutcome = "firing" | "recovered" | "not_evaluated";

export function alertOutcome(alert: Pick<Alert, "state" | "resolve_reason">): AlertOutcome {
  if (alert.state === "firing") return "firing";
  return alert.resolve_reason === "recovered" ? "recovered" : "not_evaluated";
}

export const OUTCOME_LABELS: Record<AlertOutcome, string> = {
  firing: "Firing",
  recovered: "Resolved",
  not_evaluated: "No longer evaluated",
};

/** Whole seconds it fired: until it resolved, or until `now` while it still
 * is. `now` defaults to the real current time but is overridable for tests. */
export function firingSeconds(
  alert: Pick<Alert, "fired_at" | "resolved_at">,
  now: number = Date.now() / 1000,
): number {
  return Math.max(0, Math.round((alert.resolved_at ?? now) - alert.fired_at));
}

/** "cpu.usage{core=0}", the way the API's notifications name a series, or
 * just the metric name when it has no tags. */
export function describeSeries(alert: Pick<Alert, "metric_name" | "tags">): string {
  const tags = Object.keys(alert.tags)
    .sort()
    .map((key) => `${key}=${alert.tags[key]}`)
    .join(",");
  return tags ? `${alert.metric_name}{${tags}}` : alert.metric_name;
}

/** 95 -> "95 percent", 37.123456 -> "37.1235 percent" (6 significant digits). */
export function formatAlertValue(value: number, unit: string): string {
  const number = Number.isInteger(value) ? String(value) : String(Number(value.toPrecision(6)));
  return `${number} ${unit}`;
}
