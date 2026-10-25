import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError, fetchAlerts, fetchHosts } from "../api/client";
import type { Alert, AlertHistoryResponse, AlertState } from "../api/types";
import { OUTCOME_LABELS, alertOutcome, describeSeries, firingSeconds, formatAlertValue } from "../alerts/format";
import { formatRelativeTime } from "../metrics/format";
import { describeCondition, formatDuration } from "../rules/format";

type HistoryState =
  | { status: "loading" }
  | { status: "loaded"; history: AlertHistoryResponse }
  | { status: "error"; message: string };

export const ALERTS_POLL_INTERVAL_MS = 5000;
export const ALERTS_PAGE_SIZE = 100;
// The API's own cap on `limit`.
export const MAX_ALERTS = 1000;

const STATE_FILTERS: { value: AlertState | null; label: string }[] = [
  { value: null, label: "All" },
  { value: "firing", label: "Firing" },
  { value: "resolved", label: "Resolved" },
];

function stateFromParam(value: string | null): AlertState | null {
  return value === "firing" || value === "resolved" ? value : null;
}

/** The alert history (DASH-09). The filters live in the URL
 * (`/alerts?state=firing&host=web-1`), so a filtered view can be linked to
 * and the back button undoes a filter change. */
export function AlertsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const host = searchParams.get("host");
  const state = stateFromParam(searchParams.get("state"));
  const [hosts, setHosts] = useState<string[]>([]);

  useEffect(() => {
    // Only feeds the host picker; if it fails, the picker offers "All hosts"
    // (plus any host already in the URL) and the page works otherwise.
    fetchHosts()
      .then((list) => setHosts(list.map((h) => h.hostname)))
      .catch(() => setHosts([]));
  }, []);

  const setFilter = (name: "host" | "state", value: string | null) => {
    const next = new URLSearchParams(searchParams);
    if (value === null) next.delete(name);
    else next.set(name, value);
    setSearchParams(next);
  };

  const hostOptions = host !== null && !hosts.includes(host) ? [host, ...hosts] : hosts;

  return (
    <section>
      <div className="page-header">
        <h1>Alerts</h1>
      </div>

      <div className="alert-filters">
        <div className="time-range-selector" role="group" aria-label="State">
          {STATE_FILTERS.map((option) => (
            <button
              key={option.label}
              type="button"
              className="time-range-button"
              aria-pressed={option.value === state}
              onClick={() => setFilter("state", option.value)}
            >
              {option.label}
            </button>
          ))}
        </div>
        <label className="alert-filters-host">
          Host
          <select value={host ?? ""} onChange={(e) => setFilter("host", e.target.value || null)}>
            <option value="">All hosts</option>
            {hostOptions.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
      </div>

      {/* Keyed by the filters, so changing one starts over from a loading
          state and the first page instead of showing the old results. */}
      <AlertHistoryList key={`${host ?? ""}\u0000${state ?? ""}`} host={host} state={state} />
    </section>
  );
}

function AlertHistoryList({ host, state }: { host: string | null; state: AlertState | null }) {
  const [history, setHistory] = useState<HistoryState>({ status: "loading" });
  const [limit, setLimit] = useState(ALERTS_PAGE_SIZE);

  useEffect(() => {
    let cancelled = false;
    // Same pattern as the fleet page: only the first load can show an
    // error; after that a failed poll is skipped so the list stays put.
    // "Show more" restarts this effect, so the list already on screen
    // counts as loaded too.
    let hasLoadedOnce = false;

    const poll = () => {
      fetchAlerts({ host: host ?? undefined, state: state ?? undefined, limit })
        .then((response) => {
          if (cancelled) return;
          hasLoadedOnce = true;
          setHistory({ status: "loaded", history: response });
        })
        .catch((error: unknown) => {
          if (cancelled || hasLoadedOnce) return;
          const message = error instanceof ApiError ? error.message : "Could not reach the ingestion API.";
          setHistory((current) => (current.status === "loaded" ? current : { status: "error", message }));
        });
    };

    poll();
    const interval = setInterval(poll, ALERTS_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [host, state, limit]);

  if (history.status === "loading") return <p className="page-status">Loading...</p>;
  if (history.status === "error") {
    return (
      <p role="alert" className="page-status page-status--error">
        {history.message}
      </p>
    );
  }

  const { alerts, has_more, firing_count } = history.history;
  // Recomputed on every poll's re-render, so a firing alert's duration ticks up.
  const now = Date.now() / 1000;
  const scope = host === null ? "" : ` on ${host}`;

  return (
    <>
      <p className={`alert-summary${firing_count > 0 ? " alert-summary--firing" : ""}`}>
        {firing_count === 0
          ? `Nothing is firing${scope} right now.`
          : `${firing_count} ${firing_count === 1 ? "alert" : "alerts"} firing${scope} now.`}
      </p>

      {alerts.length === 0 ? (
        <p className="page-status">
          {host === null && state === null ? "No alerts have fired yet." : "No alerts match these filters."}
        </p>
      ) : (
        <ul className="alert-list">
          {alerts.map((alert) => (
            <AlertCard key={alert.id} alert={alert} now={now} />
          ))}
        </ul>
      )}

      {has_more &&
        (limit < MAX_ALERTS ? (
          <button
            type="button"
            className="button alert-show-more"
            onClick={() => setLimit((current) => Math.min(current + ALERTS_PAGE_SIZE, MAX_ALERTS))}
          >
            Show more
          </button>
        ) : (
          <p className="page-status">Showing the latest {MAX_ALERTS}. Filter by host or state to see older ones.</p>
        ))}
    </>
  );
}

const VALUE_LABELS = { firing: "Latest value", recovered: "Value when resolved", not_evaluated: "Last value seen" };

function AlertCard({ alert, now }: { alert: Alert; now: number }) {
  const outcome = alertOutcome(alert);
  const firing = outcome === "firing";

  return (
    <li className={`alert-card alert-card--${outcome}`} aria-label={`${alert.rule_name} on ${alert.host}`}>
      <div className="alert-card-header">
        <span className={`alert-badge alert-badge--${outcome}`}>{OUTCOME_LABELS[outcome]}</span>
        <h2 className="alert-card-name">{alert.rule_name}</h2>
        <span className="alert-card-host">
          on <Link to={`/hosts/${encodeURIComponent(alert.host)}`}>{alert.host}</Link>
        </span>
      </div>
      <p className="alert-card-condition">
        <code>{describeCondition(alert)}</code>
        {alert.rule_id === null && <span className="alert-card-muted"> · rule since deleted</span>}
      </p>
      <dl className="alert-card-facts">
        <div className="alert-card-fact--wide">
          <dt>Series</dt>
          <dd>
            <code>{describeSeries(alert)}</code>
          </dd>
        </div>
        <div>
          <dt>{VALUE_LABELS[outcome]}</dt>
          <dd>{formatAlertValue(alert.last_value, alert.unit)}</dd>
        </div>
        <div>
          <dt>Fired</dt>
          <dd>
            <Timestamp epoch={alert.fired_at} now={now} />
          </dd>
        </div>
        {alert.resolved_at !== null && (
          <div>
            <dt>Resolved</dt>
            <dd>
              <Timestamp epoch={alert.resolved_at} now={now} />
            </dd>
          </div>
        )}
        <div>
          <dt>{firing ? "Firing for" : "Fired for"}</dt>
          <dd>
            {formatDuration(firingSeconds(alert, now))}
            {firing && " so far"}
          </dd>
        </div>
      </dl>
      {outcome === "not_evaluated" && (
        <p className="alert-card-note">
          Closed because Pulse stopped evaluating it, not because the value recovered: the host stopped
          reporting this metric, or the rule was disabled, changed, or deleted.
        </p>
      )}
    </li>
  );
}

/** "5m ago", with the exact local time on hover. */
function Timestamp({ epoch, now }: { epoch: number; now: number }) {
  const date = new Date(epoch * 1000);
  return (
    <time dateTime={date.toISOString()} title={date.toLocaleString()}>
      {formatRelativeTime(epoch, now)}
    </time>
  );
}
