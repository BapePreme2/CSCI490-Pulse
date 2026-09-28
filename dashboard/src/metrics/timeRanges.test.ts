import { describe, expect, it } from "vitest";
import { DEFAULT_TIME_RANGE, TIME_RANGES } from "./timeRanges";

describe("TIME_RANGES", () => {
  it("offers 1h, 6h, 24h, and 7d in ascending order", () => {
    expect(TIME_RANGES.map((r) => r.label)).toEqual(["1h", "6h", "24h", "7d"]);
    for (let i = 1; i < TIME_RANGES.length; i++) {
      expect(TIME_RANGES[i].seconds).toBeGreaterThan(TIME_RANGES[i - 1].seconds);
    }
  });

  it("keeps every range under the ingest API's 30-day cap", () => {
    const thirtyDays = 30 * 24 * 3600;
    for (const range of TIME_RANGES) {
      expect(range.seconds).toBeLessThan(thirtyDays);
    }
  });

  it("defaults to the shortest range", () => {
    expect(DEFAULT_TIME_RANGE).toBe(TIME_RANGES[0]);
  });
});
