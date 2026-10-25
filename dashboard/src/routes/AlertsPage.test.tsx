import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import type { Alert, AlertHistoryResponse, FleetHost } from "../api/types";
import { ALERTS_PAGE_SIZE, ALERTS_POLL_INTERVAL_MS, AlertsPage, MAX_ALERTS } from "./AlertsPage";

const { fetchAlerts, fetchHosts } = vi.hoisted(() => ({ fetchAlerts: vi.fn(), fetchHosts: vi.fn() }));

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, fetchAlerts, fetchHosts };
});

// 2026-10-25 12:00:00 UTC
const NOW = 1_792_929_600;

function alert(overrides: Partial<Alert> = {}): Alert {
  return {
    id: 1,
    rule_id: 3,
    rule_name: "High CPU",
    host: "web-1",
    metric_name: "cpu.usage",
    unit: "percent",
    tags: { core: "0" },
    operator: ">",
    threshold: 80,
    duration_seconds: 300,
    state: "firing",
    pending_since: NOW - 600,
    fired_at: NOW - 300,
    resolved_at: null,
    resolve_reason: null,
    last_value: 95.5,
    last_evaluated_at: NOW,
    ...overrides,
  };
}

function resolvedAlert(overrides: Partial<Alert> = {}): Alert {
  return alert({
    id: 2,
    state: "resolved",
    fired_at: NOW - 3600,
    resolved_at: NOW - 3600 + 425,
    resolve_reason: "recovered",
    last_value: 41,
    ...overrides,
  });
}

function history(alerts: Alert[], overrides: Partial<AlertHistoryResponse> = {}): AlertHistoryResponse {
  return {
    alerts,
    has_more: false,
    firing_count: alerts.filter((a) => a.state === "firing").length,
    ...overrides,
  };
}

function fleetHost(hostname: string): FleetHost {
  return { hostname, first_seen_at: 0, last_seen_at: 0, status: "online", cpu_usage: null, memory_percent: null };
}

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname + location.search}</output>;
}

function renderAlerts(path = "/alerts") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/alerts" element={<AlertsPage />} />
      </Routes>
      <LocationProbe />
    </MemoryRouter>,
  );
}

async function flush(ms = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

function card(name: string) {
  return within(screen.getByRole("listitem", { name }));
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW * 1000);
  fetchHosts.mockResolvedValue([fleetHost("web-1"), fleetHost("db-1")]);
});

afterEach(() => {
  vi.useRealTimers();
  fetchAlerts.mockReset();
  fetchHosts.mockReset();
});

describe("AlertsPage", () => {
  it("shows a loading state before data arrives", () => {
    fetchAlerts.mockReturnValue(new Promise(() => {}));

    renderAlerts();

    expect(screen.getByText("Loading...")).toBeInTheDocument();
  });

  it("says when nothing has fired yet", async () => {
    fetchAlerts.mockResolvedValue(history([]));

    renderAlerts();
    await flush();

    expect(screen.getByText("Nothing is firing right now.")).toBeInTheDocument();
    expect(screen.getByText("No alerts have fired yet.")).toBeInTheDocument();
  });

  it("asks for the first page of every fired alert by default", async () => {
    fetchAlerts.mockResolvedValue(history([]));

    renderAlerts();
    await flush();

    expect(fetchAlerts).toHaveBeenCalledWith({ host: undefined, state: undefined, limit: ALERTS_PAGE_SIZE });
  });

  it("shows a firing alert with how long it has been firing so far", async () => {
    fetchAlerts.mockResolvedValue(history([alert()]));

    renderAlerts();
    await flush();

    expect(screen.getByText("1 alert firing now.")).toBeInTheDocument();
    const c = card("High CPU on web-1");
    expect(c.getByText("Firing")).toHaveClass("alert-badge--firing");
    expect(c.getByRole("heading", { name: "High CPU" })).toBeInTheDocument();
    expect(c.getByRole("link", { name: "web-1" })).toHaveAttribute("href", "/hosts/web-1");
    expect(c.getByText("cpu.usage > 80 for 5m")).toBeInTheDocument();
    expect(c.getByText("cpu.usage{core=0}")).toBeInTheDocument();
    expect(c.getByText("Latest value").nextSibling).toHaveTextContent("95.5 percent");
    expect(c.getByText("Fired").nextSibling).toHaveTextContent("5m ago");
    expect(c.getByText("Firing for").nextSibling).toHaveTextContent("5m so far");
    expect(c.queryByText("Resolved")).not.toBeInTheDocument();
  });

  it("shows a recovered alert with when it resolved and how long it fired", async () => {
    fetchAlerts.mockResolvedValue(history([resolvedAlert()]));

    renderAlerts();
    await flush();

    const c = card("High CPU on web-1");
    expect(c.getByText("Resolved", { selector: ".alert-badge" })).toHaveClass("alert-badge--recovered");
    expect(c.getByText("Value when resolved").nextSibling).toHaveTextContent("41 percent");
    expect(c.getByText("Resolved", { selector: "dt" }).nextSibling).toHaveTextContent("52m ago");
    expect(c.getByText("Fired for").nextSibling).toHaveTextContent("7m 5s");
    expect(c.queryByText(/not because the value recovered/)).not.toBeInTheDocument();
  });

  it("says a no-longer-evaluated alert did not recover", async () => {
    fetchAlerts.mockResolvedValue(history([resolvedAlert({ resolve_reason: "not_evaluated", rule_id: null })]));

    renderAlerts();
    await flush();

    const c = card("High CPU on web-1");
    expect(c.getByText("No longer evaluated")).toHaveClass("alert-badge--not_evaluated");
    expect(c.getByText("Last value seen")).toBeInTheDocument();
    expect(c.getByText(/not because the value recovered/)).toBeInTheDocument();
    expect(c.getByText("· rule since deleted", { exact: false })).toBeInTheDocument();
  });

  it("counts firing alerts on the chosen host", async () => {
    fetchAlerts.mockResolvedValue(history([], { firing_count: 2 }));

    renderAlerts("/alerts?host=web-1");
    await flush();

    expect(screen.getByText("2 alerts firing on web-1 now.")).toBeInTheDocument();
    expect(screen.getByText("No alerts match these filters.")).toBeInTheDocument();
  });

  it("reads its filters from the URL", async () => {
    fetchAlerts.mockResolvedValue(history([]));
    fetchHosts.mockResolvedValue([fleetHost("web-1")]);

    renderAlerts("/alerts?state=resolved&host=gone-1");
    await flush();

    expect(fetchAlerts).toHaveBeenCalledWith({ host: "gone-1", state: "resolved", limit: ALERTS_PAGE_SIZE });
    expect(screen.getByRole("button", { name: "Resolved" })).toHaveAttribute("aria-pressed", "true");
    // A host that's no longer listed still shows as the selected one.
    expect(screen.getByLabelText("Host")).toHaveValue("gone-1");
  });

  it("ignores a state filter it doesn't know", async () => {
    fetchAlerts.mockResolvedValue(history([]));

    renderAlerts("/alerts?state=pending");
    await flush();

    expect(fetchAlerts).toHaveBeenCalledWith({ host: undefined, state: undefined, limit: ALERTS_PAGE_SIZE });
    expect(screen.getByRole("button", { name: "All" })).toHaveAttribute("aria-pressed", "true");
  });

  it("filters by state and host, keeping the choice in the URL", async () => {
    fetchAlerts.mockResolvedValue(history([]));

    renderAlerts();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Firing" }));
    await flush();
    fireEvent.change(screen.getByLabelText("Host"), { target: { value: "db-1" } });
    await flush();

    expect(screen.getByTestId("location")).toHaveTextContent("/alerts?state=firing&host=db-1");
    expect(fetchAlerts).toHaveBeenLastCalledWith({ host: "db-1", state: "firing", limit: ALERTS_PAGE_SIZE });
    expect(screen.getByRole("button", { name: "Firing" })).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByRole("button", { name: "All" }));
    fireEvent.change(screen.getByLabelText("Host"), { target: { value: "" } });
    await flush();

    expect(screen.getByTestId("location")).toHaveTextContent(/^\/alerts$/);
  });

  it("shows the old results only until a new filter's results arrive", async () => {
    fetchAlerts.mockResolvedValueOnce(history([alert()])).mockReturnValueOnce(new Promise(() => {}));

    renderAlerts();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Resolved" }));
    await flush();

    expect(screen.getByText("Loading...")).toBeInTheDocument();
    expect(screen.queryByRole("listitem")).not.toBeInTheDocument();
  });

  it("loads more alerts on request, keeping the current ones on screen meanwhile", async () => {
    fetchAlerts
      .mockResolvedValueOnce(history([alert()], { has_more: true }))
      .mockReturnValueOnce(new Promise(() => {}));

    renderAlerts();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Show more" }));
    await flush();

    expect(fetchAlerts).toHaveBeenLastCalledWith({ host: undefined, state: undefined, limit: 2 * ALERTS_PAGE_SIZE });
    expect(screen.getByRole("listitem", { name: "High CPU on web-1" })).toBeInTheDocument();
  });

  it("stops offering more at the API's limit", async () => {
    fetchAlerts.mockResolvedValue(history([alert()], { has_more: true }));

    renderAlerts();
    await flush();
    for (let shown = ALERTS_PAGE_SIZE; shown < MAX_ALERTS; shown += ALERTS_PAGE_SIZE) {
      fireEvent.click(screen.getByRole("button", { name: "Show more" }));
      await flush();
    }

    expect(fetchAlerts).toHaveBeenLastCalledWith({ host: undefined, state: undefined, limit: MAX_ALERTS });
    expect(screen.queryByRole("button", { name: "Show more" })).not.toBeInTheDocument();
    expect(screen.getByText(/Showing the latest 1000/)).toBeInTheDocument();
  });

  it("still works when the host list can't be loaded", async () => {
    fetchHosts.mockRejectedValue(new ApiError("down", 503));
    fetchAlerts.mockResolvedValue(history([alert()]));

    renderAlerts();
    await flush();

    expect(screen.getByRole("listitem", { name: "High CPU on web-1" })).toBeInTheDocument();
    expect(within(screen.getByLabelText("Host")).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "All hosts",
    ]);
  });

  it("shows the API's error message when the first load fails", async () => {
    fetchAlerts.mockRejectedValue(new ApiError("boom", 503));

    renderAlerts();
    await flush();

    expect(screen.getByRole("alert")).toHaveTextContent("boom");
  });

  describe("live polling", () => {
    it("refreshes on an interval, so new alerts and durations update", async () => {
      fetchAlerts.mockResolvedValueOnce(history([alert()])).mockResolvedValueOnce(
        history([alert({ id: 5, rule_name: "Disk full", metric_name: "disk.usage", tags: {} }), alert()]),
      );

      renderAlerts();
      await flush();
      expect(card("High CPU on web-1").getByText("Firing for").nextSibling).toHaveTextContent("5m so far");

      await flush(ALERTS_POLL_INTERVAL_MS);

      expect(screen.getByRole("listitem", { name: "Disk full on web-1" })).toBeInTheDocument();
      expect(card("High CPU on web-1").getByText("Firing for").nextSibling).toHaveTextContent("5m 5s so far");
    });

    it("keeps showing the last good list when a later poll fails", async () => {
      fetchAlerts.mockResolvedValueOnce(history([alert()])).mockRejectedValueOnce(new ApiError("blip", 503));

      renderAlerts();
      await flush();
      await flush(ALERTS_POLL_INTERVAL_MS);

      expect(screen.getByRole("listitem", { name: "High CPU on web-1" })).toBeInTheDocument();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it("stops polling once unmounted", async () => {
      fetchAlerts.mockResolvedValue(history([]));

      const { unmount } = renderAlerts();
      await flush();
      const calls = fetchAlerts.mock.calls.length;
      unmount();

      await flush(ALERTS_POLL_INTERVAL_MS * 3);

      expect(fetchAlerts.mock.calls.length).toBe(calls);
    });
  });
});
