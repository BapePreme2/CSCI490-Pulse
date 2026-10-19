import { describe, expect, it } from "vitest";
import type { AlertRule } from "../api/types";
import { emptyRuleForm, formFromRule, inputFromForm } from "./form";
import type { RuleFormState } from "./form";

function form(overrides: Partial<RuleFormState> = {}): RuleFormState {
  return { ...emptyRuleForm(), name: "High CPU", metricName: "cpu.usage", threshold: "80", ...overrides };
}

const RULE: AlertRule = {
  id: 3,
  name: "Root disk",
  metric_name: "disk.usage",
  operator: ">=",
  threshold: 90,
  duration_seconds: 600,
  host: "web-1",
  tag_filter: { mount: "/", core: null },
  enabled: false,
  created_at: 1,
  updated_at: 2,
};

describe("emptyRuleForm", () => {
  it("defaults to fleet-wide, enabled, > for 5 minutes", () => {
    const empty = emptyRuleForm();

    expect([empty.host, empty.enabled, empty.operator, empty.durationSeconds]).toEqual(["", true, ">", "300"]);
  });
});

describe("formFromRule / inputFromForm", () => {
  it("round-trips an existing rule unchanged", () => {
    const result = inputFromForm(formFromRule(RULE));

    expect(result).toEqual({
      ok: true,
      input: {
        name: "Root disk",
        metric_name: "disk.usage",
        operator: ">=",
        threshold: 90,
        duration_seconds: 600,
        host: "web-1",
        tag_filter: { mount: "/", core: null },
        enabled: false,
      },
    });
  });

  it("turns the fleet-wide choice into host null and trims text", () => {
    const result = inputFromForm(form({ name: "  High CPU ", metricName: " cpu.usage", host: "" }));

    expect(result.ok && [result.input.name, result.input.metric_name, result.input.host]).toEqual([
      "High CPU",
      "cpu.usage",
      null,
    ]);
  });

  it("maps 'must be absent' rows to null and ignores fully blank rows", () => {
    const result = inputFromForm(
      form({
        tagFilter: [
          { key: "core", absent: true, value: "ignored" },
          { key: "", absent: false, value: "" },
          { key: "environment", absent: false, value: "prod" },
        ],
      }),
    );

    expect(result.ok && result.input.tag_filter).toEqual({ core: null, environment: "prod" });
  });

  it.each([
    [{ name: " " }, "Name is required."],
    [{ metricName: "" }, "Metric is required."],
    [{ threshold: "" }, "Threshold must be a number."],
    [{ threshold: "high" }, "Threshold must be a number."],
    [{ durationSeconds: "" }, "Duration must be a whole number of seconds, 0 or more."],
    [{ durationSeconds: "1.5" }, "Duration must be a whole number of seconds, 0 or more."],
    [{ durationSeconds: "-5" }, "Duration must be a whole number of seconds, 0 or more."],
    [{ tagFilter: [{ key: "", absent: false, value: "/" }] }, "Every tag filter needs a tag name."],
    [{ tagFilter: [{ key: " ", absent: true, value: "" }] }, "Every tag filter needs a tag name."],
    [
      {
        tagFilter: [
          { key: "core", absent: true, value: "" },
          { key: "core", absent: false, value: "0" },
        ],
      },
      'Tag "core" is in the filter twice.',
    ],
  ])("rejects %j", (overrides, message) => {
    expect(inputFromForm(form(overrides as Partial<RuleFormState>))).toEqual({ ok: false, errors: [message] });
  });

  it("reports every problem at once", () => {
    const result = inputFromForm(form({ name: "", threshold: "x" }));

    expect(result.ok ? [] : result.errors).toHaveLength(2);
  });

  it("accepts a zero duration and a negative threshold", () => {
    const result = inputFromForm(form({ durationSeconds: "0", threshold: "-1.5" }));

    expect(result.ok && [result.input.duration_seconds, result.input.threshold]).toEqual([0, -1.5]);
  });
});
