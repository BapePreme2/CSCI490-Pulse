import { describe, expect, it } from "vitest";
import type { LatestMetric } from "../api/types";
import { findWithoutTag, metricsNamed } from "./select";

function metric(name: string, tags: Record<string, string> = {}, value = 1): LatestMetric {
  return { name, unit: "unit", tags, value, timestamp: 0 };
}

describe("findWithoutTag", () => {
  it("finds the entry lacking the given tag key among several that have it", () => {
    const metrics = [metric("cpu.usage", { core: "0" }), metric("cpu.usage", {}, 42)];

    expect(findWithoutTag(metrics, "cpu.usage", "core")?.value).toBe(42);
  });

  it("still matches when the entry carries an unrelated tag, e.g. an agent's configured environment", () => {
    const metrics = [
      metric("cpu.usage", { core: "0", environment: "demo" }),
      metric("cpu.usage", { environment: "demo" }, 42),
    ];

    expect(findWithoutTag(metrics, "cpu.usage", "core")?.value).toBe(42);
  });

  it("returns undefined when every entry under that name has the tag", () => {
    expect(findWithoutTag([metric("cpu.usage", { core: "0" })], "cpu.usage", "core")).toBeUndefined();
  });

  it("returns undefined when the name does not exist at all", () => {
    expect(findWithoutTag([], "cpu.usage", "core")).toBeUndefined();
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
