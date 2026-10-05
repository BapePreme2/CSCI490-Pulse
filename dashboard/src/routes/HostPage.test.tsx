import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, HostNotFoundError } from "../api/client";
import type { HistorySeries, HostSummary, LatestMetric } from "../api/types";
import { HostPage, TILE_POLL_INTERVAL_MS } from "./HostPage";

function metric(value: number): LatestMetric {
  return { name: "cpu.usage", unit: "percent", tags: {}, value, timestamp: 0 };
}

function summary(overrides: Partial<HostSummary> = {}): HostSummary {
  return {
    hostname: "web-1",
    first_seen_at: 0,
    last_seen_at: 0,
    status: "online",
    cpu_usage: null,
    memory_percent: null,
    ...overrides,
  };
}

async function flush(ms = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

const { fetchLatestMetrics, fetchMetricHistory, fetchHostSummary } = vi.hoisted(() => ({
  fetchLatestMetrics: vi.fn(),
  fetchMetricHistory: vi.fn(),
  fetchHostSummary: vi.fn(),
}));

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, fetchLatestMetrics, fetchMetricHistory, fetchHostSummary };
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
  // chart's and summary's fetches a harmless default so they don't hang
  // those tests.
  fetchMetricHistory.mockResolvedValue({ host: "web-1", name: "cpu.usage", start: 0, end: 0, series: [] });
  fetchHostSummary.mockResolvedValue(summary());
});

afterEach(() => {
  fetchLatestMetrics.mockReset();
  fetchMetricHistory.mockReset();
  fetchHostSummary.mockReset();
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
    expect(fetchMetricHistory).toHaveBeenCalledWith("web-1", "cpu.usage", expect.any(Number), expect.any(Number));

    renderHost("web-2");
    await waitFor(() => expect(fetchLatestMetrics).toHaveBeenCalledWith("web-2"));
    expect(fetchMetricHistory).toHaveBeenCalledWith("web-2", "cpu.usage", expect.any(Number), expect.any(Number));
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

  describe("time range selector", () => {
    beforeEach(() => {
      vi.useFakeTimers();
      vi.setSystemTime(new Date("2024-01-01T00:00:00Z"));
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it("fetches the default 1h range on first load, with its button pressed", async () => {
      fetchLatestMetrics.mockResolvedValue([]);
      fetchMetricHistory.mockResolvedValue({ host: "web-1", name: "cpu.usage", start: 0, end: 0, series: [] });

      renderHost("web-1");
      await flush();

      const now = Date.now() / 1000;
      expect(fetchMetricHistory).toHaveBeenCalledWith("web-1", "cpu.usage", now - 3600, now);
      expect(screen.getByRole("button", { name: "1h" })).toHaveAttribute("aria-pressed", "true");
    });

    it("refetches with the new range and updates the heading when a different button is clicked", async () => {
      fetchLatestMetrics.mockResolvedValue([]);
      fetchMetricHistory.mockResolvedValue({ host: "web-1", name: "cpu.usage", start: 0, end: 0, series: [] });

      renderHost("web-1");
      await flush();
      fetchMetricHistory.mockClear();

      fireEvent.click(screen.getByRole("button", { name: "24h" }));
      await flush();

      const now = Date.now() / 1000;
      expect(fetchMetricHistory).toHaveBeenCalledWith("web-1", "cpu.usage", now - 24 * 3600, now);
      expect(screen.getByRole("button", { name: "24h" })).toHaveAttribute("aria-pressed", "true");
      expect(screen.getByRole("button", { name: "1h" })).toHaveAttribute("aria-pressed", "false");
      expect(screen.getByRole("heading", { name: /cpu usage \(last 24h\)/i })).toBeInTheDocument();
    });

    it("does not disturb tile polling when the time range changes", async () => {
      fetchLatestMetrics.mockResolvedValue([metric(10)]);
      fetchMetricHistory.mockResolvedValue({ host: "web-1", name: "cpu.usage", start: 0, end: 0, series: [] });

      renderHost("web-1");
      await flush();
      const tileCallsBefore = fetchLatestMetrics.mock.calls.length;

      fireEvent.click(screen.getByRole("button", { name: "7d" }));
      await flush();

      expect(fetchLatestMetrics.mock.calls.length).toBe(tileCallsBefore);
    });
  });

  describe("status badge", () => {
    it("shows online with its last-seen time", async () => {
      fetchLatestMetrics.mockResolvedValue([]);
      fetchHostSummary.mockResolvedValue(summary({ status: "online", last_seen_at: Date.now() / 1000 }));

      renderHost("web-1");

      await waitFor(() => expect(screen.getByText("online")).toBeInTheDocument());
      expect(screen.getByText("0s ago")).toBeInTheDocument();
    });

    it("shows offline", async () => {
      fetchLatestMetrics.mockResolvedValue([]);
      fetchHostSummary.mockResolvedValue(summary({ status: "offline" }));

      renderHost("web-1");

      await waitFor(() => expect(screen.getByText("offline")).toBeInTheDocument());
    });

    it("shows no badge at all if the summary fetch fails, rather than a second error message", async () => {
      fetchLatestMetrics.mockResolvedValue([]);
      fetchHostSummary.mockRejectedValue(new ApiError("boom", 503));

      renderHost("web-1");

      await waitFor(() => expect(screen.getByText("web-1")).toBeInTheDocument());
      expect(screen.queryByText("online")).not.toBeInTheDocument();
      expect(screen.queryByText("offline")).not.toBeInTheDocument();
    });

    it("shows no badge for an unknown host either", async () => {
      fetchLatestMetrics.mockRejectedValue(new HostNotFoundError("nope"));
      fetchHostSummary.mockRejectedValue(new HostNotFoundError("nope"));

      renderHost("nope");

      await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
      expect(screen.queryByText("online")).not.toBeInTheDocument();
      expect(screen.queryByText("offline")).not.toBeInTheDocument();
    });
  });
});
