import { useId } from "react";

/** A point to call out on its series' line, e.g. an anomalous sample
 * (DASH-10). `label` is its hover tooltip. */
export interface LineChartMarker {
  timestamp: number;
  value: number;
  label: string;
}

export interface LineChartSeries {
  label: string;
  points: { timestamp: number; value: number }[];
  color?: string;
  /** Points of this series to mark. Drawn in the series' color. */
  markers?: LineChartMarker[];
}

const DEFAULT_COLORS = ["#2563eb", "#dc2626", "#16a34a", "#d97706", "#7c3aed", "#0891b2"];

function formatTime(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function LineChart({
  series,
  width = 480,
  height = 200,
  unit,
  markerLabel = "marked",
}: {
  series: LineChartSeries[];
  width?: number;
  height?: number;
  unit?: string;
  /** The legend's name for markers, shown once any series has one. */
  markerLabel?: string;
}) {
  const titleId = useId();
  const allPoints = series.flatMap((s) => s.points);

  if (allPoints.length === 0) {
    return <p className="line-chart-empty">No data yet</p>;
  }

  const colorOf = (s: LineChartSeries, index: number) => s.color ?? DEFAULT_COLORS[index % DEFAULT_COLORS.length];
  const markerCount = series.reduce((count, s) => count + (s.markers?.length ?? 0), 0);

  const padding = { top: 10, right: 10, bottom: 24, left: 44 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;

  const minTime = Math.min(...allPoints.map((p) => p.timestamp));
  const maxTime = Math.max(...allPoints.map((p) => p.timestamp));
  const values = allPoints.map((p) => p.value);
  const minValue = Math.min(0, ...values);
  const maxValue = Math.max(...values, minValue + 1);

  const scaleX = (t: number) =>
    padding.left + (maxTime === minTime ? plotWidth / 2 : ((t - minTime) / (maxTime - minTime)) * plotWidth);
  const scaleY = (v: number) => padding.top + plotHeight - ((v - minValue) / (maxValue - minValue)) * plotHeight;

  return (
    <div className="line-chart-wrapper">
      <svg className="line-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-labelledby={titleId}>
        <title id={titleId}>
          {series.map((s) => s.label).join(", ")} over time{unit ? ` (${unit})` : ""}
          {markerCount > 0 ? `, ${markerCount} ${markerCount === 1 ? "point" : "points"} marked as ${markerLabel}` : ""}
        </title>

        <line
          x1={padding.left}
          y1={padding.top}
          x2={padding.left}
          y2={height - padding.bottom}
          className="line-chart-axis"
        />
        <line
          x1={padding.left}
          y1={height - padding.bottom}
          x2={width - padding.right}
          y2={height - padding.bottom}
          className="line-chart-axis"
        />

        <text x={padding.left - 6} y={scaleY(maxValue)} textAnchor="end" dominantBaseline="hanging" className="line-chart-label">
          {maxValue.toFixed(1)}
        </text>
        <text x={padding.left - 6} y={scaleY(minValue)} textAnchor="end" dominantBaseline="auto" className="line-chart-label">
          {minValue.toFixed(1)}
        </text>

        <text x={padding.left} y={height - 4} textAnchor="start" className="line-chart-label">
          {formatTime(minTime)}
        </text>
        <text x={width - padding.right} y={height - 4} textAnchor="end" className="line-chart-label">
          {formatTime(maxTime)}
        </text>

        {series.map((s, index) => {
          if (s.points.length === 0) return null;
          const path = [...s.points]
            .sort((a, b) => a.timestamp - b.timestamp)
            .map((p) => `${scaleX(p.timestamp)},${scaleY(p.value)}`)
            .join(" ");
          return (
            <polyline
              key={s.label}
              points={path}
              fill="none"
              stroke={colorOf(s, index)}
              strokeWidth={2}
              data-testid={`line-${s.label}`}
            />
          );
        })}

        {/* After every line, so no other series' line crosses a marker. */}
        {series.map((s, index) =>
          (s.markers ?? []).map((m) => (
            <g key={`${s.label}@${m.timestamp}`} className="line-chart-marker" data-testid={`marker-${s.label}`}>
              <title>{m.label}</title>
              {/* A bigger, invisible hover target than the dot itself. */}
              <circle cx={scaleX(m.timestamp)} cy={scaleY(m.value)} r={9} className="line-chart-marker-target" />
              <circle cx={scaleX(m.timestamp)} cy={scaleY(m.value)} r={5} fill={colorOf(s, index)} className="line-chart-marker-dot" />
            </g>
          )),
        )}
      </svg>

      {(series.length > 1 || markerCount > 0) && (
        <ul className="line-chart-legend">
          {series.length > 1 &&
            series.map((s, index) => (
              <li key={s.label}>
                <span className="line-chart-legend-swatch" style={{ backgroundColor: colorOf(s, index) }} />
                {s.label}
              </li>
            ))}
          {markerCount > 0 && (
            <li>
              <span className="line-chart-legend-marker" />
              {markerLabel}
            </li>
          )}
        </ul>
      )}
    </div>
  );
}
