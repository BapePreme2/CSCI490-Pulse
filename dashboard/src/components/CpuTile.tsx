import type { LatestMetric } from "../api/types";
import { formatPercent } from "../metrics/format";
import { findUntagged, metricsNamed } from "../metrics/select";
import { MetricTile } from "./MetricTile";

export function CpuTile({ metrics }: { metrics: LatestMetric[] }) {
  const overall = findUntagged(metrics, "cpu.usage");
  const perCore = metricsNamed(metrics, "cpu.usage")
    .filter((m) => "core" in m.tags)
    .sort((a, b) => Number(a.tags.core) - Number(b.tags.core));

  if (!overall) {
    return (
      <MetricTile title="CPU">
        <p className="metric-tile-empty">No data yet</p>
      </MetricTile>
    );
  }

  return (
    <MetricTile title="CPU">
      <p className="metric-tile-headline">{formatPercent(overall.value)}</p>
      {perCore.length > 0 && (
        <ul className="metric-tile-list">
          {perCore.map((m) => (
            <li key={m.tags.core}>
              core {m.tags.core}: {formatPercent(m.value)}
            </li>
          ))}
        </ul>
      )}
    </MetricTile>
  );
}
