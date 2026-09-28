import type { LatestMetric } from "../api/types";
import { formatMb, formatPercent } from "../metrics/format";
import { findUntagged } from "../metrics/select";
import { MetricTile } from "./MetricTile";

export function MemoryTile({ metrics }: { metrics: LatestMetric[] }) {
  const total = findUntagged(metrics, "memory.total");
  const used = findUntagged(metrics, "memory.used");

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
