import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, fetchHosts } from "../api/client";
import type { FleetHost } from "../api/types";
import { formatPercent, formatRelativeTime } from "../metrics/format";

type FleetState =
  | { status: "loading" }
  | { status: "loaded"; hosts: FleetHost[] }
  | { status: "error"; message: string };

export const FLEET_POLL_INTERVAL_MS = 5000;

export function FleetOverviewPage() {
  const [state, setState] = useState<FleetState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    // Same pattern as HostPage: only the first load can show an error; once
    // the list has loaded once, a later poll that fails is skipped silently
    // so the table doesn't flicker away.
    let hasLoadedOnce = false;

    const poll = () => {
      fetchHosts()
        .then((hosts) => {
          if (cancelled) return;
          hasLoadedOnce = true;
          setState({ status: "loaded", hosts });
        })
        .catch((error: unknown) => {
          if (cancelled || hasLoadedOnce) return;
          if (error instanceof ApiError) {
            setState({ status: "error", message: error.message });
          } else {
            setState({ status: "error", message: "Could not reach the ingestion API." });
          }
        });
    };

    poll();
    const interval = setInterval(poll, FLEET_POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  return (
    <section>
      <h1>Fleet Overview</h1>
      {state.status === "loading" && <p className="page-status">Loading...</p>}
      {state.status === "error" && (
        <p role="alert" className="page-status page-status--error">
          {state.message}
        </p>
      )}
      {state.status === "loaded" && state.hosts.length === 0 && (
        <p className="page-status">No hosts have reported yet.</p>
      )}
      {state.status === "loaded" && state.hosts.length > 0 && (
        <table className="fleet-table">
          <thead>
            <tr>
              <th>Host</th>
              <th>Last seen</th>
              <th>CPU</th>
              <th>Memory</th>
            </tr>
          </thead>
          <tbody>
            {state.hosts.map((host) => (
              <tr key={host.hostname}>
                <td>
                  <Link to={`/hosts/${encodeURIComponent(host.hostname)}`}>{host.hostname}</Link>
                </td>
                <td>{formatRelativeTime(host.last_seen_at)}</td>
                <td>{host.cpu_usage === null ? "–" : formatPercent(host.cpu_usage)}</td>
                <td>{host.memory_percent === null ? "–" : formatPercent(host.memory_percent)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
