import { describe, expect, it } from "vitest";
import { formatMb, formatMbps, formatPercent, formatRelativeTime } from "./format";

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

describe("formatRelativeTime", () => {
  const now = 1_000_000;

  it("shows seconds under a minute", () => {
    expect(formatRelativeTime(now - 5, now)).toBe("5s ago");
  });

  it("shows minutes under an hour", () => {
    expect(formatRelativeTime(now - 125, now)).toBe("2m ago");
  });

  it("shows hours under a day", () => {
    expect(formatRelativeTime(now - 2 * 3600 - 1, now)).toBe("2h ago");
  });

  it("shows days at or beyond a day", () => {
    expect(formatRelativeTime(now - 2 * 86400, now)).toBe("2d ago");
  });

  it("never shows negative time for a timestamp slightly in the future (clock skew)", () => {
    expect(formatRelativeTime(now + 5, now)).toBe("0s ago");
  });
});
