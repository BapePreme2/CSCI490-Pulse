import { describe, expect, it } from "vitest";
import { describeCondition, describeTagFilter, formatDuration } from "./format";

describe("formatDuration", () => {
  it.each([
    [0, "0s"],
    [45, "45s"],
    [60, "1m"],
    [90, "1m 30s"],
    [300, "5m"],
    [3600, "1h"],
    [5400, "1h 30m"],
    [3661, "1h 1m 1s"],
    [86400, "24h"],
  ])("formats %i seconds as %s", (seconds, expected) => {
    expect(formatDuration(seconds)).toBe(expected);
  });
});

describe("describeCondition", () => {
  it("includes the sustained duration", () => {
    expect(describeCondition({ metric_name: "cpu.usage", operator: ">", threshold: 80, duration_seconds: 300 })).toBe(
      "cpu.usage > 80 for 5m",
    );
  });

  it("omits the duration for a rule that fires on the first sample", () => {
    expect(describeCondition({ metric_name: "disk.free", operator: "<", threshold: 2.5, duration_seconds: 0 })).toBe(
      "disk.free < 2.5",
    );
  });
});

describe("describeTagFilter", () => {
  it("is empty for no filter", () => {
    expect(describeTagFilter({})).toBe("");
  });

  it("describes required values and absent tags", () => {
    expect(describeTagFilter({ core: null, mount: "/" })).toBe("no core tag, mount = /");
  });
});
