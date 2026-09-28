import type { LatestMetric } from "../api/types";

/** The one series of `name` with no tags at all, e.g. overall CPU usage as
 * opposed to its per-core readings. */
export function findUntagged(metrics: LatestMetric[], name: string): LatestMetric | undefined {
  return metrics.find((m) => m.name === name && Object.keys(m.tags).length === 0);
}

/** Every series reported under `name`, tagged or not. */
export function metricsNamed(metrics: LatestMetric[], name: string): LatestMetric[] {
  return metrics.filter((m) => m.name === name);
}
