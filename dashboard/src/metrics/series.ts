import type { HistorySeries } from "../api/types";
import type { LineChartSeries } from "../components/LineChart";

/** A short human label built from only the tag keys that actually
 * distinguish this metric's series (e.g. "core" for cpu.usage). Any other,
 * incidental tag (an agent's configured `environment`, say) is ignored, or
 * every series would carry it and the labels would all say the same thing
 * alongside the part that actually varies. No relevant keys present (or
 * none given) means "overall". */
export function seriesLabel(tags: Record<string, string>, relevantKeys?: string[]): string {
  const entries = relevantKeys
    ? relevantKeys.filter((key) => key in tags).map((key): [string, string] => [key, tags[key]])
    : Object.entries(tags);
  if (entries.length === 0) return "overall";
  return entries.map(([key, value]) => `${key} ${value}`).join(", ");
}

export function toChartSeries(series: HistorySeries[], relevantKeys?: string[]): LineChartSeries[] {
  return series.map((s) => ({ label: seriesLabel(s.tags, relevantKeys), points: s.points }));
}
