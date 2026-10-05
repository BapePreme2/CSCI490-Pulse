import { act, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import type { FleetHost } from "../api/types";
import { FLEET_POLL_INTERVAL_MS, FleetOverviewPage } from "./FleetOverviewPage";

function host(overrides: Partial<FleetHost> = {}): FleetHost {
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

const { fetchHosts } = vi.hoisted(() => ({ fetchHosts: vi.fn() }));

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, fetchHosts };
});

function renderFleet() {
  return render(
    <MemoryRouter>
      <FleetOverviewPage />
    </MemoryRouter>,
  );
}

afterEach(() => {
  fetchHosts.mockReset();
});

describe("FleetOverviewPage", () => {
  it("shows a loading state before data arrives", () => {
    fetchHosts.mockReturnValue(new Promise(() => {}));

    renderFleet();

    expect(screen.getByText("Loading...")).toBeInTheDocument();
  });

  it("shows a message when no hosts have reported", async () => {
    fetchHosts.mockResolvedValue([]);

    renderFleet();

    await waitFor(() => expect(screen.getByText("No hosts have reported yet.")).toBeInTheDocument());
  });

  it("lists each host, linking to its page", async () => {
    fetchHosts.mockResolvedValue([
      host({ hostname: "web-1", cpu_usage: 42.5, memory_percent: 60 }),
      host({ hostname: "web-2" }),
    ]);

    renderFleet();

    await waitFor(() => expect(screen.getByRole("link", { name: "web-1" })).toBeInTheDocument());
    expect(screen.getByRole("link", { name: "web-1" })).toHaveAttribute("href", "/hosts/web-1");
    expect(screen.getByRole("link", { name: "web-2" })).toHaveAttribute("href", "/hosts/web-2");
    expect(screen.getByText("42.5%")).toBeInTheDocument();
    expect(screen.getByText("60.0%")).toBeInTheDocument();
  });

  it("shows a dash for a host with no key metrics yet, instead of blank", async () => {
    fetchHosts.mockResolvedValue([host({ cpu_usage: null, memory_percent: null })]);

    renderFleet();

    await waitFor(() => expect(screen.getAllByText("–")).toHaveLength(2));
  });

  it("shows each host's online/offline status", async () => {
    fetchHosts.mockResolvedValue([
      host({ hostname: "web-1", status: "online" }),
      host({ hostname: "web-2", status: "offline" }),
    ]);

    renderFleet();

    await waitFor(() => expect(screen.getByText("online")).toBeInTheDocument());
    expect(screen.getByText("offline")).toBeInTheDocument();
  });

  it("shows the API's error message when the request fails", async () => {
    fetchHosts.mockRejectedValue(new ApiError("boom", 503));

    renderFleet();

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("boom"));
  });

  it("shows a generic message for a non-API failure", async () => {
    fetchHosts.mockRejectedValue(new Error("unexpected"));

    renderFleet();

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("Could not reach the ingestion API."),
    );
  });

  describe("live polling", () => {
    beforeEach(() => {
      vi.useFakeTimers();
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it("refreshes the host list on an interval", async () => {
      fetchHosts
        .mockResolvedValueOnce([host({ cpu_usage: 10 })])
        .mockResolvedValueOnce([host({ cpu_usage: 20 })]);

      renderFleet();
      await flush();
      expect(screen.getByText("10.0%")).toBeInTheDocument();

      await flush(FLEET_POLL_INTERVAL_MS);

      expect(screen.getByText("20.0%")).toBeInTheDocument();
      expect(fetchHosts).toHaveBeenCalledTimes(2);
    });

    it("keeps showing the last good list when a later poll fails", async () => {
      fetchHosts.mockResolvedValueOnce([host({ cpu_usage: 10 })]).mockRejectedValueOnce(new ApiError("blip", 503));

      renderFleet();
      await flush();
      expect(screen.getByText("10.0%")).toBeInTheDocument();

      await flush(FLEET_POLL_INTERVAL_MS);

      expect(screen.getByText("10.0%")).toBeInTheDocument();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it("stops polling once unmounted", async () => {
      fetchHosts.mockResolvedValue([host()]);

      const { unmount } = renderFleet();
      await flush();
      const callsBeforeUnmount = fetchHosts.mock.calls.length;
      unmount();

      await flush(FLEET_POLL_INTERVAL_MS * 3);

      expect(fetchHosts.mock.calls.length).toBe(callsBeforeUnmount);
    });
  });
});
