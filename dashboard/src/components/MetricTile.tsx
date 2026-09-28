import type { ReactNode } from "react";

export function MetricTile({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="metric-tile">
      <h2 className="metric-tile-title">{title}</h2>
      <div className="metric-tile-body">{children}</div>
    </div>
  );
}
