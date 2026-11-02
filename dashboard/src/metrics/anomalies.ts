import type { Anomaly, AnomalyDirection, AnomalyPoint, AnomalySeries, HistorySeries } from "../api/types";
import type { LineChartSeries } from "../components/LineChart";
import { seriesLabel, toChartSeries } from "./series";

/** The same tag set gives the same key whatever order its keys arrive in. */
export function tagsKey(tags: Record<string, string>): string {
  return JSON.stringify(
    Object.keys(tags)
      .sort()
      .map((key) => [key, tags[key]]),
  );
}

/** 98.6123 -> "98.6", 12 -> "12", 2.0302 -> "2.03", 16384.2 -> "16384". */
export function formatAmount(value: number): string {
  return Math.abs(value) >= 1000 ? String(Math.round(value)) : String(Number(value.toPrecision(3)));
}

/** "98.6%" for a percent metric, "3 count" otherwise. */
export function formatValue(value: number, unit: string): string {
  return unit === "percent" ? `${formatAmount(value)}%` : `${formatAmount(value)} ${unit}`;
}

/** How far off a sample was: "+11.3σ from 12 ± 2.03%", or, against a
 * baseline that was constantly 0 (no spread to measure in), "baseline was
 * constantly 0". */
export function formatDeviation(point: AnomalyPoint, unit: string): string {
  if (point.deviation === null) return `baseline was constantly ${formatValue(point.mean, unit)}`;
  const sign = point.deviation >= 0 ? "+" : "−";
  return `${sign}${Math.abs(point.deviation).toFixed(1)}σ from ${formatAmount(point.mean)} ± ${formatValue(point.spread, unit)}`;
}

/** How far a point is from its baseline, in σ either way; an infinite
 * (null) deviation is furthest of all. */
export function distanceOf(point: AnomalyPoint): number {
  return point.deviation === null ? Infinity : Math.abs(point.deviation);
}

/** The point furthest from its baseline. */
export function peakOf(anomaly: Anomaly): AnomalyPoint {
  return anomaly.points.reduce((peak, p) => (distanceOf(p) > distanceOf(peak) ? p : peak));
}

/** A clock time, with the weekday too when the range is longer than a day. */
export function formatWhen(epochSeconds: number, rangeSeconds: number): string {
  const options: Intl.DateTimeFormatOptions = { hour: "2-digit", minute: "2-digit", second: "2-digit" };
  if (rangeSeconds > 86400) options.weekday = "short";
  return new Date(epochSeconds * 1000).toLocaleString([], options);
}

/** The history chart's series, each with its anomalous samples as markers.
 * Anomalies are matched to a line by tag set, and only samples the line
 * actually has are marked, so a marker always sits on its line (the two
 * requests can see slightly different data). */
export function toChartSeriesWithAnomalies(
  history: HistorySeries[],
  anomalies: AnomalySeries[],
  relevantKeys?: string[],
  rangeSeconds = 3600,
): LineChartSeries[] {
  const byTags = new Map(anomalies.map((s) => [tagsKey(s.tags), s]));
  return toChartSeries(history, relevantKeys).map((chartSeries, i) => {
    const found = byTags.get(tagsKey(history[i].tags));
    if (!found) return chartSeries;
    const onLine = new Set(history[i].points.map((p) => p.timestamp));
    const markers = found.anomalies.flatMap((anomaly) =>
      anomaly.points
        .filter((p) => onLine.has(p.timestamp))
        .map((p) => ({
          timestamp: p.timestamp,
          value: p.value,
          label:
            `${chartSeries.label}, ${formatWhen(p.timestamp, rangeSeconds)}: ` +
            `${formatValue(p.value, found.unit)}, ${formatDeviation(p, found.unit)}`,
        })),
    );
    return markers.length > 0 ? { ...chartSeries, markers } : chartSeries;
  });
}

/** One anomalous event: every series' runs that overlap in time. */
export interface AnomalyNote {
  key: string;
  start: number;
  end: number;
  /** The series anomalous during it, in the API's (the chart's) order. */
  series: string[];
  directions: AnomalyDirection[];
  /** How many anomalous samples, across every series. */
  samples: number;
  /** The sample furthest from its baseline, and its series. */
  peak: { series: string; unit: string; point: AnomalyPoint };
}

/** The runs as events, newest first. Runs that overlap in time (within
 * `slackSeconds`, because each series' samples carry their own timestamps)
 * are one event, so a spike across every core is one note, not one per
 * core. */
export function anomalyNotes(anomalies: AnomalySeries[], relevantKeys?: string[], slackSeconds = 1): AnomalyNote[] {
  const runs = anomalies
    .flatMap((s, order) =>
      s.anomalies.map((anomaly) => ({ order, label: seriesLabel(s.tags, relevantKeys), unit: s.unit, anomaly })),
    )
    .sort((a, b) => a.anomaly.start - b.anomaly.start);

  const events: (typeof runs)[] = [];
  let eventEnd = -Infinity;
  for (const run of runs) {
    if (events.length > 0 && run.anomaly.start <= eventEnd + slackSeconds) {
      events[events.length - 1].push(run);
      eventEnd = Math.max(eventEnd, run.anomaly.end);
    } else {
      events.push([run]);
      eventEnd = run.anomaly.end;
    }
  }

  return events
    .map((event) => {
      const inOrder = [...event].sort((a, b) => a.order - b.order);
      const furthest = event.reduce((best, run) =>
        distanceOf(peakOf(run.anomaly)) > distanceOf(peakOf(best.anomaly)) ? run : best,
      );
      return {
        key: `${event[0].anomaly.start}`,
        start: event[0].anomaly.start,
        end: Math.max(...event.map((run) => run.anomaly.end)),
        series: [...new Set(inOrder.map((run) => run.label))],
        directions: [...new Set(inOrder.map((run) => run.anomaly.direction))],
        samples: event.reduce((count, run) => count + run.anomaly.points.length, 0),
        peak: { series: furthest.label, unit: furthest.unit, point: peakOf(furthest.anomaly) },
      };
    })
    .reverse();
}
