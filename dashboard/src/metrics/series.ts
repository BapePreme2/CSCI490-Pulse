import type { HistorySeries } from "../api/types";
import type { LineChartSeries } from "../components/LineChart";

/** A short human label for a series' tags, e.g. {core: "0"} -> "core 0",
 * or "overall" for an untagged series. */
export function seriesLabel(tags: Record<string, string>): string {
  const entries = Object.entries(tags);
  if (entries.length === 0) return "overall";
  return entries.map(([key, value]) => `${key} ${value}`).join(", ");
}

export function toChartSeries(series: HistorySeries[]): LineChartSeries[] {
  return series.map((s) => ({ label: seriesLabel(s.tags), points: s.points }));
}
