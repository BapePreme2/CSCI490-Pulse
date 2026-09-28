import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { ApiError, HostNotFoundError, fetchLatestMetrics, fetchMetricHistory } from "../api/client";
import type { HistorySeries, LatestMetric } from "../api/types";
import { CpuTile } from "../components/CpuTile";
import { DiskTile } from "../components/DiskTile";
import { LineChart } from "../components/LineChart";
import { MemoryTile } from "../components/MemoryTile";
import { NetworkTile } from "../components/NetworkTile";
import { toChartSeries } from "../metrics/series";

type LatestState =
  | { status: "loading" }
  | { status: "loaded"; metrics: LatestMetric[] }
  | { status: "not-found" }
  | { status: "error"; message: string };

type HistoryState =
  | { status: "loading" }
  | { status: "loaded"; series: HistorySeries[] }
  | { status: "error"; message: string };

export const TILE_POLL_INTERVAL_MS = 5000;

export function HostPage() {
  const { hostname } = useParams<{ hostname: string }>();
  const [latestState, setLatestState] = useState<LatestState>({ status: "loading" });
  const [historyState, setHistoryState] = useState<HistoryState>({ status: "loading" });

  useEffect(() => {
    if (!hostname) return;

    let cancelled = false;
    // Only the very first load shows loading/not-found/error; once tiles
    // have shown real data, a later poll failing (a brief network blip)
    // just gets skipped so the last good values stay on screen.
    let hasLoadedOnce = false;
    setLatestState({ status: "loading" });

    const poll = () => {
      fetchLatestMetrics(hostname)
        .then((metrics) => {
          if (cancelled) return;
          hasLoadedOnce = true;
          setLatestState({ status: "loaded", metrics });
        })
        .catch((error: unknown) => {
          if (cancelled || hasLoadedOnce) return;
          if (error instanceof HostNotFoundError) {
            setLatestState({ status: "not-found" });
          } else if (error instanceof ApiError) {
            setLatestState({ status: "error", message: error.message });
          } else {
            setLatestState({ status: "error", message: "Could not reach the ingestion API." });
          }
        });
    };

    poll();
    const interval = setInterval(poll, TILE_POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [hostname]);

  useEffect(() => {
    if (!hostname) return;

    let cancelled = false;
    setHistoryState({ status: "loading" });

    fetchMetricHistory(hostname, "cpu.usage")
      .then((history) => {
        if (!cancelled) setHistoryState({ status: "loaded", series: history.series });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        // An unknown host is already surfaced by the tiles above; here it's
        // just "no CPU history yet", same as an empty series list.
        if (error instanceof HostNotFoundError) {
          setHistoryState({ status: "loaded", series: [] });
        } else if (error instanceof ApiError) {
          setHistoryState({ status: "error", message: error.message });
        } else {
          setHistoryState({ status: "error", message: "Could not reach the ingestion API." });
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
      {latestState.status === "loading" && <p>Loading...</p>}
      {latestState.status === "not-found" && (
        <p role="alert">No data has been reported yet for &quot;{hostname}&quot;.</p>
      )}
      {latestState.status === "error" && <p role="alert">{latestState.message}</p>}
      {latestState.status === "loaded" && (
        <div className="tile-grid">
          <CpuTile metrics={latestState.metrics} />
          <MemoryTile metrics={latestState.metrics} />
          <DiskTile metrics={latestState.metrics} />
          <NetworkTile metrics={latestState.metrics} />
        </div>
      )}

      <section className="chart-section">
        <h2>CPU usage (last hour)</h2>
        {historyState.status === "loading" && <p>Loading chart...</p>}
        {historyState.status === "error" && <p role="alert">{historyState.message}</p>}
        {historyState.status === "loaded" && (
          <LineChart series={toChartSeries(historyState.series)} unit="%" />
        )}
      </section>
    </section>
  );
}
