import type { AlertRule, AlertRuleInput, Operator } from "../api/types";

export const OPERATORS: Operator[] = [">", ">=", "<", "<="];

/** One editable row of a rule's tag filter. `absent` means "this tag must not be present". */
export interface TagFilterRow {
  key: string;
  absent: boolean;
  value: string;
}

/** The rule form's own state: text fields stay strings until submit. */
export interface RuleFormState {
  name: string;
  metricName: string;
  operator: Operator;
  threshold: string;
  durationSeconds: string;
  /** "" = fleet-wide. */
  host: string;
  tagFilter: TagFilterRow[];
  enabled: boolean;
}

export function emptyRuleForm(): RuleFormState {
  return {
    name: "",
    metricName: "",
    operator: ">",
    threshold: "",
    durationSeconds: "300",
    host: "",
    tagFilter: [],
    enabled: true,
  };
}

export function formFromRule(rule: AlertRule): RuleFormState {
  return {
    name: rule.name,
    metricName: rule.metric_name,
    operator: rule.operator,
    threshold: String(rule.threshold),
    durationSeconds: String(rule.duration_seconds),
    host: rule.host ?? "",
    tagFilter: Object.entries(rule.tag_filter).map(([key, value]) => ({
      key,
      absent: value === null,
      value: value ?? "",
    })),
    enabled: rule.enabled,
  };
}

export type FormResult = { ok: true; input: AlertRuleInput } | { ok: false; errors: string[] };

/**
 * Turns the form into the API's shape, catching what's easy to catch here
 * (blank fields, non-numbers, duplicate filter keys). The API validates
 * everything again and its 422 field errors are shown the same way.
 */
export function inputFromForm(form: RuleFormState): FormResult {
  const errors: string[] = [];
  const name = form.name.trim();
  const metricName = form.metricName.trim();
  if (!name) errors.push("Name is required.");
  if (!metricName) errors.push("Metric is required.");

  const threshold = Number(form.threshold);
  if (form.threshold.trim() === "" || !Number.isFinite(threshold)) {
    errors.push("Threshold must be a number.");
  }

  const duration = Number(form.durationSeconds);
  if (form.durationSeconds.trim() === "" || !Number.isInteger(duration) || duration < 0) {
    errors.push("Duration must be a whole number of seconds, 0 or more.");
  }

  const tagFilter: Record<string, string | null> = {};
  for (const row of form.tagFilter) {
    const key = row.key.trim();
    if (!key) {
      // A row left completely blank is just unused; anything else needs a key.
      if (row.absent || row.value !== "") errors.push("Every tag filter needs a tag name.");
      continue;
    }
    if (key in tagFilter) {
      errors.push(`Tag "${key}" is in the filter twice.`);
      continue;
    }
    tagFilter[key] = row.absent ? null : row.value;
  }

  if (errors.length > 0) return { ok: false, errors };
  return {
    ok: true,
    input: {
      name,
      metric_name: metricName,
      operator: form.operator,
      threshold,
      duration_seconds: duration,
      host: form.host === "" ? null : form.host,
      tag_filter: tagFilter,
      enabled: form.enabled,
    },
  };
}
