import { describe, expect, it } from "vitest";
import { alertOutcome, describeSeries, firingSeconds, formatAlertValue } from "./format";

describe("alertOutcome", () => {
  it("tells firing, recovered, and no-longer-evaluated apart", () => {
    expect(alertOutcome({ state: "firing", resolve_reason: null })).toBe("firing");
    expect(alertOutcome({ state: "resolved", resolve_reason: "recovered" })).toBe("recovered");
    expect(alertOutcome({ state: "resolved", resolve_reason: "not_evaluated" })).toBe("not_evaluated");
  });
});

describe("firingSeconds", () => {
  it("measures a resolved alert from firing to resolving", () => {
    expect(firingSeconds({ fired_at: 1000, resolved_at: 1425.4 }, 99999)).toBe(425);
  });

  it("measures a firing alert up to now", () => {
    expect(firingSeconds({ fired_at: 1000, resolved_at: null }, 1300)).toBe(300);
  });

  it("never goes negative when the browser's clock is behind the API's", () => {
    expect(firingSeconds({ fired_at: 1000, resolved_at: null }, 990)).toBe(0);
  });
});

describe("describeSeries", () => {
  it("names the series with its tags sorted, like the notifications do", () => {
    expect(describeSeries({ metric_name: "disk.usage", tags: { mount: "/", environment: "demo" } })).toBe(
      "disk.usage{environment=demo,mount=/}",
    );
  });

  it("is just the metric name without tags", () => {
    expect(describeSeries({ metric_name: "memory.used", tags: {} })).toBe("memory.used");
  });
});

describe("formatAlertValue", () => {
  it.each([
    [95, "percent", "95 percent"],
    [93.25, "percent", "93.25 percent"],
    [37.123456789, "percent", "37.1235 percent"],
    [16000000000, "bytes", "16000000000 bytes"],
  ])("formats %d %s as %s", (value, unit, expected) => {
    expect(formatAlertValue(value, unit)).toBe(expected);
  });
});
