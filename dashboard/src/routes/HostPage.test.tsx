import { act, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, HostNotFoundError } from "../api/client";
import type { HistorySeries, LatestMetric } from "../api/types";
import { HostPage, TILE_POLL_INTERVAL_MS } from "./HostPage";

function metric(value: number): LatestMetric {
  return { name: "cpu.usage", unit: "percent", tags: {}, value, timestamp: 0 };
}

async function flush(ms = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

const { fetchLatestMetrics, fetchMetricHistory } = vi.hoisted(() => ({
  fetchLatestMetrics: vi.fn(),
  fetchMetricHistory: vi.fn(),
}));

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, fetchLatestMetrics, fetchMetricHistory };
});

function renderHost(hostname: string) {
  return render(
    <MemoryRouter initialEntries={[`/hosts/${hostname}`]}>
      <Routes>
        <Route path="/hosts/:hostname" element={<HostPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  // Most tests only care about the tiles (fetchLatestMetrics); give the
  // chart's fetch a harmless default so it doesn't hang those tests.
  fetchMetricHistory.mockResolvedValue({ host: "web-1", name: "cpu.usage", start: 0, end: 0, series: [] });
});

afterEach(() => {
  fetchLatestMetrics.mockReset();
  fetchMetricHistory.mockReset();
});

describe("HostPage", () => {
  it("shows a loading state before data arrives", () => {
    fetchLatestMetrics.mockReturnValue(new Promise(() => {}));

    renderHost("web-1");

    expect(screen.getByText("Loading...")).toBeInTheDocument();
  });

  it("renders tiles once metrics load", async () => {
    const metrics: LatestMetric[] = [{ name: "cpu.usage", unit: "percent", tags: {}, value: 12.5, timestamp: 0 }];
    fetchLatestMetrics.mockResolvedValue(metrics);

    renderHost("web-1");

    await waitFor(() => expect(screen.getByText("12.5%")).toBeInTheDocument());
  });

  it("shows a not-found message for an unknown host", async () => {
    fetchLatestMetrics.mockRejectedValue(new HostNotFoundError("nope"));

    renderHost("nope");

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/no data has been reported/i),
    );
  });

  it("shows the API's error message when the request fails", async () => {
    fetchLatestMetrics.mockRejectedValue(new ApiError("boom", 503));

    renderHost("web-1");

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("boom"));
  });

  it("shows a generic message for a non-API failure", async () => {
    fetchLatestMetrics.mockRejectedValue(new Error("unexpected"));

    renderHost("web-1");

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("Could not reach the ingestion API."),
    );
  });

  it("re-fetches when the hostname in the URL changes", async () => {
    fetchLatestMetrics.mockResolvedValue([]);

    renderHost("web-1");
    await waitFor(() => expect(fetchLatestMetrics).toHaveBeenCalledWith("web-1"));
    expect(fetchMetricHistory).toHaveBeenCalledWith("web-1", "cpu.usage");

    renderHost("web-2");
    await waitFor(() => expect(fetchLatestMetrics).toHaveBeenCalledWith("web-2"));
    expect(fetchMetricHistory).toHaveBeenCalledWith("web-2", "cpu.usage");
  });

  describe("CPU history chart", () => {
    it("shows a loading state before the chart data arrives", () => {
      fetchLatestMetrics.mockReturnValue(new Promise(() => {}));
      fetchMetricHistory.mockReturnValue(new Promise(() => {}));

      renderHost("web-1");

      expect(screen.getByText("Loading chart...")).toBeInTheDocument();
    });

    it("renders the chart once history loads", async () => {
      fetchLatestMetrics.mockResolvedValue([]);
      const series: HistorySeries[] = [
        { unit: "percent", tags: { core: "0" }, points: [{ timestamp: 1, value: 42 }] },
      ];
      fetchMetricHistory.mockResolvedValue({ host: "web-1", name: "cpu.usage", start: 0, end: 1, series });

      renderHost("web-1");

      await waitFor(() => expect(screen.getByTestId("line-core 0")).toBeInTheDocument());
    });

    it("treats an unknown host as simply no chart data, not an error", async () => {
      fetchLatestMetrics.mockRejectedValue(new HostNotFoundError("nope"));
      fetchMetricHistory.mockRejectedValue(new HostNotFoundError("nope"));

      renderHost("nope");

      await waitFor(() => expect(screen.getByText("No data yet")).toBeInTheDocument());
    });

    it("shows an error message when the chart's request fails", async () => {
      fetchLatestMetrics.mockResolvedValue([]);
      fetchMetricHistory.mockRejectedValue(new ApiError("chart boom", 503));

      renderHost("web-1");

      await waitFor(() => {
        const alerts = screen.getAllByRole("alert");
        expect(alerts.some((el) => el.textContent === "chart boom")).toBe(true);
      });
    });
  });

  describe("live tile polling", () => {
    beforeEach(() => {
      vi.useFakeTimers();
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it("refreshes tile data every TILE_POLL_INTERVAL_MS", async () => {
      fetchLatestMetrics.mockResolvedValueOnce([metric(10)]).mockResolvedValueOnce([metric(20)]);

      renderHost("web-1");
      await flush();
      expect(screen.getByText("10.0%")).toBeInTheDocument();

      await flush(TILE_POLL_INTERVAL_MS);

      expect(screen.getByText("20.0%")).toBeInTheDocument();
      expect(fetchLatestMetrics).toHaveBeenCalledTimes(2);
    });

    it("keeps showing the last good tiles when a later poll fails", async () => {
      fetchLatestMetrics.mockResolvedValueOnce([metric(10)]).mockRejectedValueOnce(new ApiError("blip", 503));

      renderHost("web-1");
      await flush();
      expect(screen.getByText("10.0%")).toBeInTheDocument();

      await flush(TILE_POLL_INTERVAL_MS);

      expect(screen.getByText("10.0%")).toBeInTheDocument();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it("recovers automatically once a not-found host starts reporting", async () => {
      fetchLatestMetrics.mockRejectedValueOnce(new HostNotFoundError("web-1")).mockResolvedValueOnce([metric(30)]);

      renderHost("web-1");
      await flush();
      expect(screen.getByRole("alert")).toHaveTextContent(/no data has been reported/i);

      await flush(TILE_POLL_INTERVAL_MS);

      expect(screen.getByText("30.0%")).toBeInTheDocument();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it("stops polling once unmounted", async () => {
      fetchLatestMetrics.mockResolvedValue([metric(10)]);

      const { unmount } = renderHost("web-1");
      await flush();
      const callsBeforeUnmount = fetchLatestMetrics.mock.calls.length;
      unmount();

      await flush(TILE_POLL_INTERVAL_MS * 3);

      expect(fetchLatestMetrics.mock.calls.length).toBe(callsBeforeUnmount);
    });

    it("does not leak the previous host's polling interval when the hostname changes", async () => {
      fetchLatestMetrics.mockResolvedValue([metric(10)]);

      const { unmount } = renderHost("web-1");
      await flush();
      unmount();
      fetchLatestMetrics.mockClear();

      renderHost("web-2");
      await flush();
      const callsAfterSwitch = fetchLatestMetrics.mock.calls.length;

      await flush(TILE_POLL_INTERVAL_MS);

      // Only web-2's interval should have fired once more; a leaked web-1
      // interval would inflate this beyond a single extra call.
      expect(fetchLatestMetrics.mock.calls.length).toBe(callsAfterSwitch + 1);
      expect(fetchLatestMetrics).toHaveBeenCalledWith("web-2");
    });
  });
});
