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

  it("joins multiple tags when no relevant-key filter is given", () => {
    expect(seriesLabel({ mount: "/", device: "/dev/sda" })).toBe("mount /, device /dev/sda");
  });

  it("with a relevant-key filter, ignores tags outside it", () => {
    // Regression: an agent's configured environment tag (or any other
    // incidental tag) must not clutter every series' label.
    expect(seriesLabel({ core: "0", environment: "demo" }, ["core"])).toBe("core 0");
  });

  it("with a relevant-key filter, labels as overall when none of those keys are present", () => {
    expect(seriesLabel({ environment: "demo" }, ["core"])).toBe("overall");
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

  it("applies a relevant-key filter to every series", () => {
    const series: HistorySeries[] = [
      { unit: "percent", tags: { core: "0", environment: "demo" }, points: [] },
      { unit: "percent", tags: { environment: "demo" }, points: [] },
    ];

    const chart = toChartSeries(series, ["core"]);

    expect(chart.map((s) => s.label)).toEqual(["core 0", "overall"]);
  });
});
