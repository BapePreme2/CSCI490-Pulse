import type { AlertRule, AlertRuleInput, Operator, RuleKind } from "../api/types";

export const OPERATORS: Operator[] = [">", ">=", "<", "<="];

/** Same limits as the API's baseline_seconds. */
export const MIN_BASELINE_SECONDS = 60;
export const MAX_BASELINE_SECONDS = 86400;
export const DEFAULT_BASELINE_SECONDS = 3600;

/** Whether an operator means "above" (> or >=) rather than "below". */
export function isAbove(operator: Operator): boolean {
  return operator === ">" || operator === ">=";
}

/** One editable row of a rule's tag filter. `absent` means "this tag must not be present". */
export interface TagFilterRow {
  key: string;
  absent: boolean;
  value: string;
}

/** The rule form's own state: text fields stay strings until submit. For an
 * anomaly rule, `threshold` is the number of σ as a positive number; the
 * operator says which side of the baseline, and the sign is added on submit. */
export interface RuleFormState {
  kind: RuleKind;
  name: string;
  metricName: string;
  operator: Operator;
  threshold: string;
  baselineSeconds: string;
  durationSeconds: string;
  /** "" = fleet-wide. */
  host: string;
  tagFilter: TagFilterRow[];
  enabled: boolean;
}

export function emptyRuleForm(): RuleFormState {
  return {
    kind: "threshold",
    name: "",
    metricName: "",
    operator: ">",
    threshold: "",
    baselineSeconds: String(DEFAULT_BASELINE_SECONDS),
    durationSeconds: "300",
    host: "",
    tagFilter: [],
    enabled: true,
  };
}

export function formFromRule(rule: AlertRule): RuleFormState {
  return {
    kind: rule.kind,
    name: rule.name,
    metricName: rule.metric_name,
    operator: rule.operator,
    threshold: String(rule.kind === "anomaly" ? Math.abs(rule.threshold) : rule.threshold),
    baselineSeconds: String(rule.baseline_seconds ?? DEFAULT_BASELINE_SECONDS),
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

  const anomaly = form.kind === "anomaly";
  const threshold = Number(form.threshold);
  if (form.threshold.trim() === "" || !Number.isFinite(threshold)) {
    errors.push(anomaly ? "Sigma must be a number." : "Threshold must be a number.");
  } else if (anomaly && threshold <= 0) {
    errors.push("Sigma must be more than 0 (choose above or below with the operator).");
  }

  const baseline = Number(form.baselineSeconds);
  if (
    anomaly &&
    (form.baselineSeconds.trim() === "" ||
      !Number.isInteger(baseline) ||
      baseline < MIN_BASELINE_SECONDS ||
      baseline > MAX_BASELINE_SECONDS)
  ) {
    errors.push(`Baseline window must be a whole number of seconds, ${MIN_BASELINE_SECONDS}-${MAX_BASELINE_SECONDS}.`);
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
      threshold: anomaly && !isAbove(form.operator) ? -threshold : threshold,
      duration_seconds: duration,
      host: form.host === "" ? null : form.host,
      tag_filter: tagFilter,
      enabled: form.enabled,
      kind: form.kind,
      baseline_seconds: anomaly ? baseline : null,
    },
  };
}
