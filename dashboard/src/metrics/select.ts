import type { LatestMetric } from "../api/types";

/** The one series of `name` lacking `tagKey`, e.g. overall CPU usage (no
 * `core` tag) as opposed to its per-core readings. Any other, incidental
 * tag (an agent's configured `environment`, say) is irrelevant here -- a
 * series can carry those and still count as "the untagged one" for this
 * purpose, which is why this checks one specific key rather than requiring
 * zero tags altogether. */
export function findWithoutTag(metrics: LatestMetric[], name: string, tagKey: string): LatestMetric | undefined {
  return metrics.find((m) => m.name === name && !(tagKey in m.tags));
}

/** Every series reported under `name`, tagged or not. */
export function metricsNamed(metrics: LatestMetric[], name: string): LatestMetric[] {
  return metrics.filter((m) => m.name === name);
}
