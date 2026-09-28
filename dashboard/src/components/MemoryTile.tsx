import type { LatestMetric } from "../api/types";
import { formatMb, formatPercent } from "../metrics/format";
import { metricsNamed } from "../metrics/select";
import { MetricTile } from "./MetricTile";

export function MemoryTile({ metrics }: { metrics: LatestMetric[] }) {
  // Memory metrics have no distinguishing tag at all (unlike per-core CPU
  // or per-mount disk), so the first match is the only one.
  const total = metricsNamed(metrics, "memory.total")[0];
  const used = metricsNamed(metrics, "memory.used")[0];

  if (!total || !used || total.value <= 0) {
    return (
      <MetricTile title="Memory">
        <p className="metric-tile-empty">No data yet</p>
      </MetricTile>
    );
  }

  const percentUsed = (used.value / total.value) * 100;

  return (
    <MetricTile title="Memory">
      <p className="metric-tile-headline">{formatPercent(percentUsed)}</p>
      <p className="metric-tile-detail">
        {formatMb(used.value)} / {formatMb(total.value)}
      </p>
    </MetricTile>
  );
}
