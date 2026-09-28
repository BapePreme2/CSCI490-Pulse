import { describe, expect, it } from "vitest";
import { formatMb, formatMbps, formatPercent } from "./format";

describe("formatPercent", () => {
  it("rounds to one decimal place", () => {
    expect(formatPercent(42.567)).toBe("42.6%");
  });
});

describe("formatMb", () => {
  it("shows MB below 1024", () => {
    expect(formatMb(512)).toBe("512 MB");
  });

  it("switches to GB at or above 1024", () => {
    expect(formatMb(2048)).toBe("2.0 GB");
  });
});

describe("formatMbps", () => {
  it("shows two decimal places", () => {
    expect(formatMbps(1.23456)).toBe("1.23 MB/s");
  });
});
