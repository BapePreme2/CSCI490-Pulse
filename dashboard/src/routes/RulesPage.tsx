import { useCallback, useEffect, useState } from "react";
import { ApiError, RuleValidationError, createRule, deleteRule, fetchHosts, fetchRules, updateRule } from "../api/client";
import type { AlertRule, AlertRuleInput } from "../api/types";
import { RuleForm } from "../components/RuleForm";
import { emptyRuleForm, formFromRule } from "../rules/form";
import { describeCondition, describeTagFilter } from "../rules/format";

type RulesState =
  | { status: "loading" }
  | { status: "loaded"; rules: AlertRule[] }
  | { status: "error"; message: string };

/** Which form is open: none, a new rule, or editing an existing one. */
type Editing = null | "new" | AlertRule;

// Labels the API's field names the way the form does, for its 422 errors.
const FIELD_LABELS: Record<string, string> = {
  name: "Name",
  metric_name: "Metric",
  operator: "Operator",
  threshold: "Threshold",
  duration_seconds: "Duration",
  host: "Hosts",
  tag_filter: "Tag filter",
  enabled: "Enabled",
};

function messageFor(error: unknown): string {
  return error instanceof ApiError ? error.message : "Could not reach the ingestion API.";
}

export function RulesPage() {
  const [state, setState] = useState<RulesState>({ status: "loading" });
  const [hosts, setHosts] = useState<string[]>([]);
  const [editing, setEditing] = useState<Editing>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setState({ status: "loaded", rules: await fetchRules() });
    } catch (error: unknown) {
      setState({ status: "error", message: messageFor(error) });
    }
  }, []);

  useEffect(() => {
    void load();
    // The host list only feeds the scope picker; if it fails, the picker
    // still offers "All hosts" and the page works otherwise.
    fetchHosts()
      .then((list) => setHosts(list.map((h) => h.hostname)))
      .catch(() => setHosts([]));
  }, [load]);

  const save = async (input: AlertRuleInput): Promise<string[]> => {
    try {
      if (editing === "new") {
        await createRule(input);
      } else if (editing) {
        await updateRule(editing.id, input);
      }
    } catch (error: unknown) {
      if (error instanceof RuleValidationError && error.errors.length > 0) {
        return error.errors.map((e) => `${FIELD_LABELS[e.field] ?? e.field}: ${e.message}`);
      }
      return [messageFor(error)];
    }
    setEditing(null);
    await load();
    return [];
  };

  const remove = async (rule: AlertRule) => {
    if (!window.confirm(`Delete the rule "${rule.name}"? Alerts it already raised stay in the history.`)) {
      return;
    }
    setActionError(null);
    try {
      await deleteRule(rule.id);
    } catch (error: unknown) {
      setActionError(`Could not delete "${rule.name}": ${messageFor(error)}`);
    }
    if (editing !== null && editing !== "new" && editing.id === rule.id) setEditing(null);
    await load();
  };

  return (
    <section>
      <div className="page-header">
        <h1>Alert rules</h1>
        {editing === null && (
          <button type="button" className="button button--primary" onClick={() => setEditing("new")}>
            New rule
          </button>
        )}
      </div>

      {editing !== null && (
        <RuleForm
          key={editing === "new" ? "new" : editing.id}
          title={editing === "new" ? "New rule" : `Edit "${editing.name}"`}
          initial={editing === "new" ? emptyRuleForm() : formFromRule(editing)}
          hosts={hosts}
          submitLabel={editing === "new" ? "Create rule" : "Save changes"}
          onSubmit={save}
          onCancel={() => setEditing(null)}
        />
      )}

      {actionError && (
        <p role="alert" className="page-status page-status--error">
          {actionError}
        </p>
      )}

      {state.status === "loading" && <p className="page-status">Loading...</p>}
      {state.status === "error" && (
        <p role="alert" className="page-status page-status--error">
          {state.message}
        </p>
      )}
      {state.status === "loaded" && state.rules.length === 0 && (
        <p className="page-status">No alert rules yet. Create one to start alerting.</p>
      )}
      {state.status === "loaded" && state.rules.length > 0 && (
        <ul className="rule-list">
          {state.rules.map((rule) => {
            const filter = describeTagFilter(rule.tag_filter);
            return (
              <li key={rule.id} className="rule-card" aria-label={rule.name}>
                <div className="rule-card-header">
                  <h2 className="rule-card-name">{rule.name}</h2>
                  <span className={`rule-badge rule-badge--${rule.enabled ? "enabled" : "disabled"}`}>
                    {rule.enabled ? "Enabled" : "Disabled"}
                  </span>
                </div>
                <p className="rule-card-condition">
                  <code>{describeCondition(rule)}</code>
                </p>
                <p className="rule-card-scope">
                  {rule.host ?? "All hosts"}
                  {filter && <> · {filter}</>}
                </p>
                <div className="rule-card-actions">
                  <button type="button" className="button" onClick={() => setEditing(rule)}>
                    Edit
                  </button>
                  <button type="button" className="button button--danger" onClick={() => void remove(rule)}>
                    Delete
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
