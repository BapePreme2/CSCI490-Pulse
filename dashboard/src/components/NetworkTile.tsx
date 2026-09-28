import type { LatestMetric } from "../api/types";
import { formatMbps } from "../metrics/format";
import { metricsNamed } from "../metrics/select";
import { MetricTile } from "./MetricTile";

export function NetworkTile({ metrics }: { metrics: LatestMetric[] }) {
  const sent = metricsNamed(metrics, "network.bytes_sent");
  const recv = metricsNamed(metrics, "network.bytes_recv");
  const interfaces = Array.from(
    new Set([...sent, ...recv].map((m) => m.tags.interface)),
  ).sort();

  if (interfaces.length === 0) {
    return (
      <MetricTile title="Network">
        <p className="metric-tile-empty">No data yet</p>
      </MetricTile>
    );
  }

  return (
    <MetricTile title="Network">
      <ul className="metric-tile-list">
        {interfaces.map((iface) => {
          const up = sent.find((m) => m.tags.interface === iface)?.value ?? 0;
          const down = recv.find((m) => m.tags.interface === iface)?.value ?? 0;
          return (
            <li key={iface}>
              {iface}: {formatMbps(up)} up / {formatMbps(down)} down
            </li>
          );
        })}
      </ul>
    </MetricTile>
  );
}
