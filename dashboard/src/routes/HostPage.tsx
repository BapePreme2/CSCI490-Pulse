import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import {
  ApiError,
  HostNotFoundError,
  fetchHostSummary,
  fetchLatestMetrics,
  fetchMetricAnomalies,
  fetchMetricHistory,
} from "../api/client";
import type { AnomalySeries, HistorySeries, HostSummary, LatestMetric } from "../api/types";
import { AnomalyList } from "../components/AnomalyList";
import { CpuTile } from "../components/CpuTile";
import { DiskTile } from "../components/DiskTile";
import { LineChart } from "../components/LineChart";
import { MemoryTile } from "../components/MemoryTile";
import { NetworkTile } from "../components/NetworkTile";
import { TimeRangeSelector } from "../components/TimeRangeSelector";
import { toChartSeriesWithAnomalies } from "../metrics/anomalies";
import { formatRelativeTime } from "../metrics/format";
import { DEFAULT_TIME_RANGE, TIME_RANGES, type TimeRangeOption } from "../metrics/timeRanges";

type LatestState =
  | { status: "loading" }
  | { status: "loaded"; metrics: LatestMetric[] }
  | { status: "not-found" }
  | { status: "error"; message: string };

type HistoryState =
  | { status: "loading" }
  | { status: "loaded"; series: HistorySeries[] }
  | { status: "error"; message: string };

// Markers are extra detail on the chart: if they can't be fetched, the chart
// still shows and a note says the markers are missing.
type AnomalyState =
  | { status: "loading" }
  | { status: "loaded"; series: AnomalySeries[] }
  | { status: "error"; message: string };

// This is a supplementary status badge, not primary data -- the tiles above
// already surface a real "unknown host"/error state, so any failure here
// (not-found or otherwise) just hides the badge instead of showing a second,
// redundant error message.
type SummaryState = { status: "loading" } | { status: "loaded"; summary: HostSummary } | { status: "unavailable" };

export const TILE_POLL_INTERVAL_MS = 5000;

// What the chart marks as anomalous: the same defaults as a new anomaly rule
// (3σ from the hour before each sample).
export const ANOMALY_SIGMA = 3;
export const ANOMALY_WINDOW_SECONDS = 3600;

export function HostPage() {
  const { hostname } = useParams<{ hostname: string }>();
  const [latestState, setLatestState] = useState<LatestState>({ status: "loading" });
  const [historyState, setHistoryState] = useState<HistoryState>({ status: "loading" });
  const [anomalyState, setAnomalyState] = useState<AnomalyState>({ status: "loading" });
  const [summaryState, setSummaryState] = useState<SummaryState>({ status: "loading" });
  const [timeRange, setTimeRange] = useState<TimeRangeOption>(DEFAULT_TIME_RANGE);

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

    const poll = () => {
      fetchHostSummary(hostname)
        .then((summary) => {
          if (!cancelled) setSummaryState({ status: "loaded", summary });
        })
        .catch(() => {
          if (!cancelled) setSummaryState({ status: "unavailable" });
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
    setAnomalyState({ status: "loading" });

    const end = Date.now() / 1000;
    const start = end - timeRange.seconds;

    fetchMetricAnomalies(hostname, "cpu.usage", start, end, {
      window: ANOMALY_WINDOW_SECONDS,
      sigma: ANOMALY_SIGMA,
    })
      .then((response) => {
        if (!cancelled) setAnomalyState({ status: "loaded", series: response.series });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        if (error instanceof HostNotFoundError) {
          setAnomalyState({ status: "loaded", series: [] });
        } else {
          const message = error instanceof ApiError ? error.message : "Could not reach the ingestion API.";
          setAnomalyState({ status: "error", message });
        }
      });

    fetchMetricHistory(hostname, "cpu.usage", start, end)
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
  }, [hostname, timeRange]);

  if (!hostname) {
    return null;
  }

  return (
    <section>
      <div className="host-page-header">
        <h1>{hostname}</h1>
        {summaryState.status === "loaded" && (
          <>
            <span className={`fleet-status fleet-status--${summaryState.summary.status}`}>
              <span className="fleet-status-dot" aria-hidden="true" />
              {summaryState.summary.status}
            </span>
            <span className="page-status host-page-last-seen">
              {formatRelativeTime(summaryState.summary.last_seen_at)}
            </span>
          </>
        )}
      </div>
      {latestState.status === "loading" && <p className="page-status">Loading...</p>}
      {latestState.status === "not-found" && (
        <p role="alert" className="page-status page-status--warning">
          No data has been reported yet for &quot;{hostname}&quot;.
        </p>
      )}
      {latestState.status === "error" && (
        <p role="alert" className="page-status page-status--error">
          {latestState.message}
        </p>
      )}
      {latestState.status === "loaded" && (
        <div className="tile-grid">
          <CpuTile metrics={latestState.metrics} />
          <MemoryTile metrics={latestState.metrics} />
          <DiskTile metrics={latestState.metrics} />
          <NetworkTile metrics={latestState.metrics} />
        </div>
      )}

      <section className="chart-section">
        <div className="chart-section-header">
          <h2>CPU usage (last {timeRange.label})</h2>
          <TimeRangeSelector options={TIME_RANGES} selected={timeRange} onSelect={setTimeRange} />
        </div>
        {historyState.status === "loading" && <p className="page-status">Loading chart...</p>}
        {historyState.status === "error" && (
          <p role="alert" className="page-status page-status--error">
            {historyState.message}
          </p>
        )}
        {historyState.status === "loaded" && (
          <>
            <LineChart
              series={toChartSeriesWithAnomalies(
                historyState.series,
                anomalyState.status === "loaded" ? anomalyState.series : [],
                ["core"],
                timeRange.seconds,
              )}
              unit="%"
              markerLabel={`anomaly (${ANOMALY_SIGMA}σ+)`}
            />
            {historyState.series.some((s) => s.points.length > 0) && anomalyState.status === "loaded" && (
              <AnomalyList
                anomalies={anomalyState.series}
                sigma={ANOMALY_SIGMA}
                windowSeconds={ANOMALY_WINDOW_SECONDS}
                rangeSeconds={timeRange.seconds}
                relevantKeys={["core"]}
              />
            )}
            {anomalyState.status === "error" && (
              <p className="page-status anomaly-notes-unavailable">
                Anomaly markers unavailable: {anomalyState.message}
              </p>
            )}
          </>
        )}
      </section>
    </section>
  );
}
