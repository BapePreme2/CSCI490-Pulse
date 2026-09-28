import type { LatestMetric } from "../api/types";
import { formatPercent } from "../metrics/format";
import { metricsNamed } from "../metrics/select";
import { MetricTile } from "./MetricTile";

export function DiskTile({ metrics }: { metrics: LatestMetric[] }) {
  const usages = metricsNamed(metrics, "disk.usage");
  const free = metricsNamed(metrics, "disk.free");

  if (usages.length === 0) {
    return (
      <MetricTile title="Disk">
        <p className="metric-tile-empty">No data yet</p>
      </MetricTile>
    );
  }

  const sorted = [...usages].sort((a, b) => b.value - a.value);

  return (
    <MetricTile title="Disk">
      <ul className="metric-tile-list">
        {sorted.map((m) => {
          const freeEntry = free.find((f) => f.tags.mount === m.tags.mount);
          return (
            <li key={m.tags.mount}>
              {m.tags.mount}: {formatPercent(m.value)}
              {freeEntry ? ` (${freeEntry.value.toFixed(1)} GB free)` : ""}
            </li>
          );
        })}
      </ul>
    </MetricTile>
  );
}
