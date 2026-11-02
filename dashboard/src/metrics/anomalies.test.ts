import { describe, expect, it } from "vitest";
import type { Anomaly, AnomalyPoint, AnomalySeries, HistorySeries } from "../api/types";
import {
  anomalyNotes,
  formatAmount,
  formatDeviation,
  formatValue,
  formatWhen,
  peakOf,
  tagsKey,
  toChartSeriesWithAnomalies,
} from "./anomalies";

function point(timestamp: number, value: number, deviation: number | null = 5): AnomalyPoint {
  return { timestamp, value, mean: 10, spread: 2, deviation };
}

function anomaly(...points: AnomalyPoint[]): Anomaly {
  return { start: points[0].timestamp, end: points[points.length - 1].timestamp, direction: "above", points };
}

const HISTORY: HistorySeries[] = [
  { unit: "percent", tags: { core: "0" }, points: [{ timestamp: 0, value: 10 }, { timestamp: 10, value: 11 }] },
  {
    unit: "percent",
    tags: { core: "1", environment: "lab" },
    points: [
      { timestamp: 0, value: 10 },
      { timestamp: 10, value: 90 },
      { timestamp: 20, value: 95 },
    ],
  },
];

describe("tagsKey", () => {
  it("is the same for the same tags in any order", () => {
    expect(tagsKey({ a: "1", b: "2" })).toBe(tagsKey({ b: "2", a: "1" }));
    expect(tagsKey({ a: "1" })).not.toBe(tagsKey({ a: "2" }));
  });
});

describe("toChartSeriesWithAnomalies", () => {
  it("marks each anomalous sample on the line with the same tags", () => {
    const anomalies: AnomalySeries[] = [
      { unit: "percent", tags: { environment: "lab", core: "1" }, anomalies: [anomaly(point(10, 90), point(20, 95))] },
    ];

    const [core0, core1] = toChartSeriesWithAnomalies(HISTORY, anomalies, ["core"]);

    expect(core0.markers).toBeUndefined();
    expect(core1.label).toBe("core 1");
    expect(core1.markers?.map((m) => [m.timestamp, m.value])).toEqual([
      [10, 90],
      [20, 95],
    ]);
    expect(core1.markers?.[0].label).toMatch(/^core 1, .+: 90%, \+5\.0σ from 10 ± 2%$/);
  });

  it("skips anomalous samples the line doesn't have", () => {
    const anomalies: AnomalySeries[] = [
      { unit: "percent", tags: { core: "0" }, anomalies: [anomaly(point(10, 11), point(30, 99))] },
    ];

    const [core0] = toChartSeriesWithAnomalies(HISTORY, anomalies, ["core"]);

    expect(core0.markers?.map((m) => m.timestamp)).toEqual([10]);
  });

  it("ignores anomalies of a series the chart doesn't show", () => {
    const anomalies: AnomalySeries[] = [{ unit: "percent", tags: { core: "7" }, anomalies: [anomaly(point(10, 90))] }];

    expect(toChartSeriesWithAnomalies(HISTORY, anomalies, ["core"]).every((s) => !s.markers)).toBe(true);
  });
});

describe("formatting", () => {
  it("rounds amounts to 3 significant digits, whole numbers from 1000", () => {
    expect([98.6123, 12, 2.0302, 0.5, 16384.2].map(formatAmount)).toEqual(["98.6", "12", "2.03", "0.5", "16384"]);
  });

  it("writes percent as % and other units as words", () => {
    expect(formatValue(98.61, "percent")).toBe("98.6%");
    expect(formatValue(3, "count")).toBe("3 count");
  });

  it("describes a deviation by its sign and the baseline it's from", () => {
    expect(formatDeviation({ ...point(0, 0), deviation: 11.26, mean: 12, spread: 2.03 }, "percent")).toBe(
      "+11.3σ from 12 ± 2.03%",
    );
    expect(formatDeviation({ ...point(0, 0), deviation: -4.04 }, "percent")).toBe("−4.0σ from 10 ± 2%");
  });

  it("describes any change from a constant baseline without a sigma", () => {
    expect(formatDeviation({ ...point(0, 3, null), mean: 0, spread: 0 }, "count")).toBe(
      "baseline was constantly 0 count",
    );
  });

  it("adds the weekday only for ranges longer than a day", () => {
    const t = Date.UTC(2026, 10, 2, 14, 30) / 1000;
    expect(formatWhen(t, 3600)).toBe(
      new Date(t * 1000).toLocaleString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }),
    );
    expect(formatWhen(t, 7 * 86400)).toBe(
      new Date(t * 1000).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" }),
    );
  });
});

describe("peakOf", () => {
  it("is the point furthest from its baseline in either direction", () => {
    expect(peakOf(anomaly(point(0, 1, -3), point(10, 2, -7), point(20, 3, -4))).value).toBe(2);
  });

  it("counts an infinite (null) deviation as furthest", () => {
    expect(peakOf(anomaly(point(0, 1, 50), point(10, 2, null))).value).toBe(2);
  });
});

describe("anomalyNotes", () => {
  it("makes runs that overlap in time one event, its series in the chart's order", () => {
    const notes = anomalyNotes(
      [
        { unit: "percent", tags: {}, anomalies: [anomaly(point(100.002, 1, 4), point(110.002, 1, 6))] },
        { unit: "percent", tags: { core: "0" }, anomalies: [anomaly(point(100.001, 1, 9))] },
        { unit: "percent", tags: { core: "1" }, anomalies: [anomaly(point(100.0, 1, 3))] },
      ],
      ["core"],
    );

    expect(notes).toHaveLength(1);
    const [note] = notes;
    expect(note.series).toEqual(["overall", "core 0", "core 1"]);
    expect([note.start, note.end, note.samples]).toEqual([100.0, 110.002, 4]);
    expect(note.peak.series).toBe("core 0");
    expect(note.peak.point.deviation).toBe(9);
  });

  it("joins runs within a second of each other, since each series has its own timestamps", () => {
    const notes = anomalyNotes([
      { unit: "percent", tags: { core: "0" }, anomalies: [anomaly(point(100.0, 1))] },
      { unit: "percent", tags: { core: "1" }, anomalies: [anomaly(point(100.4, 1))] },
      { unit: "percent", tags: { core: "2" }, anomalies: [anomaly(point(102.0, 1))] },
    ]);

    expect(notes.map((n) => n.series)).toEqual([["core 2"], ["core 0", "core 1"]]);
  });

  it("lists events newest first", () => {
    const notes = anomalyNotes(
      [
        { unit: "percent", tags: { core: "0" }, anomalies: [anomaly(point(100, 1)), anomaly(point(300, 1))] },
        { unit: "percent", tags: {}, anomalies: [anomaly(point(200, 1))] },
      ],
      ["core"],
    );

    expect(notes.map((n) => [n.start, n.series])).toEqual([
      [300, ["core 0"]],
      [200, ["overall"]],
      [100, ["core 0"]],
    ]);
    expect(new Set(notes.map((n) => n.key)).size).toBe(3);
  });

  it("names both directions when an event has runs above and below", () => {
    const below: Anomaly = { ...anomaly(point(100, 1, -5)), direction: "below" };
    const [note] = anomalyNotes([
      { unit: "percent", tags: { core: "0" }, anomalies: [anomaly(point(100, 1))] },
      { unit: "percent", tags: { core: "1" }, anomalies: [below] },
    ]);

    expect(note.directions).toEqual(["above", "below"]);
  });
});
