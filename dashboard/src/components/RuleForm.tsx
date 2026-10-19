import { useState } from "react";
import type { FormEvent } from "react";
import type { AlertRuleInput } from "../api/types";
import { formatDuration } from "../rules/format";
import { OPERATORS, inputFromForm } from "../rules/form";
import type { RuleFormState, TagFilterRow } from "../rules/form";

/** Metric names the Pulse agent reports, offered as suggestions only: any
 * name is accepted, since a rule can exist before anything reports it. */
export const KNOWN_METRICS = [
  "cpu.usage",
  "load.avg",
  "memory.used",
  "memory.available",
  "memory.free",
  "memory.total",
  "memory.swap_used",
  "memory.swap_free",
  "disk.usage",
  "disk.free",
  "disk.read_iops",
  "disk.write_iops",
  "disk.read_throughput",
  "disk.write_throughput",
  "network.bytes_sent",
  "network.bytes_recv",
  "network.packets_sent",
  "network.packets_recv",
  "network.errors",
  "network.drops",
];

interface RuleFormProps {
  title: string;
  initial: RuleFormState;
  /** Hostnames offered in the scope picker. */
  hosts: string[];
  submitLabel: string;
  /** Saves the rule; resolves to the error messages to show, or [] on success. */
  onSubmit: (input: AlertRuleInput) => Promise<string[]>;
  onCancel: () => void;
}

export function RuleForm({ title, initial, hosts, submitLabel, onSubmit, onCancel }: RuleFormProps) {
  const [form, setForm] = useState<RuleFormState>(initial);
  const [errors, setErrors] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);

  // The rule's own host stays selectable even if it's missing from the list.
  const hostOptions = form.host && !hosts.includes(form.host) ? [...hosts, form.host] : hosts;

  const set = <K extends keyof RuleFormState>(key: K, value: RuleFormState[K]) =>
    setForm((current) => ({ ...current, [key]: value }));

  const setRow = (index: number, change: Partial<TagFilterRow>) =>
    set(
      "tagFilter",
      form.tagFilter.map((row, i) => (i === index ? { ...row, ...change } : row)),
    );

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const result = inputFromForm(form);
    if (!result.ok) {
      setErrors(result.errors);
      return;
    }
    setErrors([]);
    setSubmitting(true);
    const failures = await onSubmit(result.input);
    // On success the page closes this form, so there's nothing to reset.
    if (failures.length > 0) {
      setErrors(failures);
      setSubmitting(false);
    }
  };

  return (
    <form className="rule-form" onSubmit={submit} aria-label={title} noValidate>
      <h2>{title}</h2>

      {errors.length > 0 && (
        <ul role="alert" className="rule-form-errors">
          {errors.map((message) => (
            <li key={message}>{message}</li>
          ))}
        </ul>
      )}

      <div className="rule-form-grid">
        <label className="rule-form-field rule-form-field--wide">
          Name
          <input value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="e.g. High CPU" />
        </label>

        <label className="rule-form-field rule-form-field--wide">
          Metric
          <input
            value={form.metricName}
            onChange={(e) => set("metricName", e.target.value)}
            list="rule-form-metrics"
            placeholder="e.g. cpu.usage"
          />
          <datalist id="rule-form-metrics">
            {KNOWN_METRICS.map((name) => (
              <option key={name} value={name} />
            ))}
          </datalist>
        </label>

        <label className="rule-form-field">
          Operator
          <select value={form.operator} onChange={(e) => set("operator", e.target.value as RuleFormState["operator"])}>
            {OPERATORS.map((op) => (
              <option key={op} value={op}>
                {op}
              </option>
            ))}
          </select>
        </label>

        <label className="rule-form-field">
          Threshold
          <input
            inputMode="decimal"
            value={form.threshold}
            onChange={(e) => set("threshold", e.target.value)}
            placeholder="e.g. 80"
          />
        </label>

        {/* Not a wrapping <label>: the hint would become part of the field's name. */}
        <div className="rule-form-field">
          <label htmlFor="rule-form-duration">For (seconds)</label>
          <input
            id="rule-form-duration"
            inputMode="numeric"
            value={form.durationSeconds}
            onChange={(e) => set("durationSeconds", e.target.value)}
            aria-describedby="rule-form-duration-hint"
          />
          <span id="rule-form-duration-hint" className="rule-form-hint">
            {durationHint(form.durationSeconds)}
          </span>
        </div>

        <label className="rule-form-field">
          Hosts
          <select value={form.host} onChange={(e) => set("host", e.target.value)}>
            <option value="">All hosts</option>
            {hostOptions.map((host) => (
              <option key={host} value={host}>
                {host}
              </option>
            ))}
          </select>
        </label>
      </div>

      <fieldset className="rule-form-tags">
        <legend>Tag filter</legend>
        <p className="rule-form-hint">
          Narrows which series the rule watches. Use "must be absent" with <code>core</code> to watch overall CPU
          instead of every core.
        </p>
        {form.tagFilter.map((row, index) => (
          <div className="rule-form-tag-row" key={index}>
            <input
              aria-label={`Tag ${index + 1} name`}
              value={row.key}
              onChange={(e) => setRow(index, { key: e.target.value })}
              placeholder="tag, e.g. core"
            />
            <select
              aria-label={`Tag ${index + 1} match`}
              value={row.absent ? "absent" : "equals"}
              onChange={(e) => setRow(index, { absent: e.target.value === "absent" })}
            >
              <option value="equals">equals</option>
              <option value="absent">must be absent</option>
            </select>
            {!row.absent && (
              <input
                aria-label={`Tag ${index + 1} value`}
                value={row.value}
                onChange={(e) => setRow(index, { value: e.target.value })}
                placeholder="value, e.g. /"
              />
            )}
            <button
              type="button"
              className="button button--ghost"
              aria-label={`Remove tag ${index + 1}`}
              onClick={() =>
                set(
                  "tagFilter",
                  form.tagFilter.filter((_, i) => i !== index),
                )
              }
            >
              Remove
            </button>
          </div>
        ))}
        <button
          type="button"
          className="button button--ghost"
          onClick={() => set("tagFilter", [...form.tagFilter, { key: "", absent: false, value: "" }])}
        >
          Add tag filter
        </button>
      </fieldset>

      <label className="rule-form-check">
        <input type="checkbox" checked={form.enabled} onChange={(e) => set("enabled", e.target.checked)} />
        Enabled
      </label>

      <div className="rule-form-actions">
        <button type="submit" className="button button--primary" disabled={submitting}>
          {submitting ? "Saving..." : submitLabel}
        </button>
        <button type="button" className="button" onClick={onCancel} disabled={submitting}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function durationHint(text: string): string {
  const seconds = Number(text);
  if (text.trim() === "" || !Number.isInteger(seconds) || seconds < 0) return "";
  return seconds === 0 ? "Fires on the first breaching sample" : `Fires after ${formatDuration(seconds)} in breach`;
}
