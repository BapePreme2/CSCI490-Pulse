import { describe, expect, it } from "vitest";
import type { LatestMetric } from "../api/types";
import { findUntagged, metricsNamed } from "./select";

function metric(name: string, tags: Record<string, string> = {}, value = 1): LatestMetric {
  return { name, unit: "unit", tags, value, timestamp: 0 };
}

describe("findUntagged", () => {
  it("finds the entry with no tags among several tagged ones", () => {
    const metrics = [metric("cpu.usage", { core: "0" }), metric("cpu.usage", {}, 42)];

    expect(findUntagged(metrics, "cpu.usage")?.value).toBe(42);
  });

  it("returns undefined when every entry under that name is tagged", () => {
    expect(findUntagged([metric("cpu.usage", { core: "0" })], "cpu.usage")).toBeUndefined();
  });

  it("returns undefined when the name does not exist at all", () => {
    expect(findUntagged([], "cpu.usage")).toBeUndefined();
  });
});

describe("metricsNamed", () => {
  it("returns every series under a name, tagged or not", () => {
    const metrics = [
      metric("cpu.usage", { core: "0" }),
      metric("cpu.usage", { core: "1" }),
      metric("memory.used"),
    ];

    expect(metricsNamed(metrics, "cpu.usage")).toHaveLength(2);
  });

  it("returns an empty array when nothing matches", () => {
    expect(metricsNamed([], "cpu.usage")).toEqual([]);
  });
});
