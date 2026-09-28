import { describe, expect, it } from "vitest";
import type { HistorySeries } from "../api/types";
import { seriesLabel, toChartSeries } from "./series";

describe("seriesLabel", () => {
  it("labels an untagged series as overall", () => {
    expect(seriesLabel({})).toBe("overall");
  });

  it("labels a single tag as 'key value'", () => {
    expect(seriesLabel({ core: "0" })).toBe("core 0");
  });

  it("joins multiple tags", () => {
    expect(seriesLabel({ mount: "/", device: "/dev/sda" })).toBe("mount /, device /dev/sda");
  });
});

describe("toChartSeries", () => {
  it("maps each series to a chart series with a derived label", () => {
    const series: HistorySeries[] = [
      { unit: "percent", tags: { core: "0" }, points: [{ timestamp: 1, value: 2 }] },
      { unit: "percent", tags: {}, points: [] },
    ];

    const chart = toChartSeries(series);

    expect(chart).toEqual([
      { label: "core 0", points: [{ timestamp: 1, value: 2 }] },
      { label: "overall", points: [] },
    ]);
  });
});
