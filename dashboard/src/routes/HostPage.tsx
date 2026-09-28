import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { ApiError, HostNotFoundError, fetchLatestMetrics } from "../api/client";
import type { LatestMetric } from "../api/types";
import { CpuTile } from "../components/CpuTile";
import { DiskTile } from "../components/DiskTile";
import { MemoryTile } from "../components/MemoryTile";
import { NetworkTile } from "../components/NetworkTile";

type LoadState =
  | { status: "loading" }
  | { status: "loaded"; metrics: LatestMetric[] }
  | { status: "not-found" }
  | { status: "error"; message: string };

export function HostPage() {
  const { hostname } = useParams<{ hostname: string }>();
  const [state, setState] = useState<LoadState>({ status: "loading" });

  useEffect(() => {
    if (!hostname) return;

    let cancelled = false;
    setState({ status: "loading" });

    fetchLatestMetrics(hostname)
      .then((metrics) => {
        if (!cancelled) setState({ status: "loaded", metrics });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        if (error instanceof HostNotFoundError) {
          setState({ status: "not-found" });
        } else if (error instanceof ApiError) {
          setState({ status: "error", message: error.message });
        } else {
          setState({ status: "error", message: "Could not reach the ingestion API." });
        }
      });

    return () => {
      cancelled = true;
    };
  }, [hostname]);

  if (!hostname) {
    return null;
  }

  return (
    <section>
      <h1>{hostname}</h1>
      {state.status === "loading" && <p>Loading...</p>}
      {state.status === "not-found" && (
        <p role="alert">No data has been reported yet for &quot;{hostname}&quot;.</p>
      )}
      {state.status === "error" && <p role="alert">{state.message}</p>}
      {state.status === "loaded" && (
        <div className="tile-grid">
          <CpuTile metrics={state.metrics} />
          <MemoryTile metrics={state.metrics} />
          <DiskTile metrics={state.metrics} />
          <NetworkTile metrics={state.metrics} />
        </div>
      )}
    </section>
  );
}
