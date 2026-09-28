import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, HostNotFoundError } from "../api/client";
import type { LatestMetric } from "../api/types";
import { HostPage } from "./HostPage";

const { fetchLatestMetrics } = vi.hoisted(() => ({ fetchLatestMetrics: vi.fn() }));

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, fetchLatestMetrics };
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

afterEach(() => {
  fetchLatestMetrics.mockReset();
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

    renderHost("web-2");
    await waitFor(() => expect(fetchLatestMetrics).toHaveBeenCalledWith("web-2"));
  });
});
