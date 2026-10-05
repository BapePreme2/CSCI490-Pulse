import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { App } from "./App";

// This file is a routing smoke test; HostPage's own data-fetching states are
// covered by src/routes/HostPage.test.tsx.
vi.mock("./api/client", () => ({
  fetchLatestMetrics: vi.fn().mockReturnValue(new Promise(() => {})),
  fetchMetricHistory: vi.fn().mockReturnValue(new Promise(() => {})),
  fetchHosts: vi.fn().mockReturnValue(new Promise(() => {})),
}));

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

describe("App routing", () => {
  it("renders the fleet overview at /", () => {
    renderAt("/");

    expect(screen.getByRole("heading", { name: /fleet overview/i })).toBeInTheDocument();
  });

  it("renders a host page with the hostname taken from the URL", () => {
    renderAt("/hosts/web-1");

    expect(screen.getByRole("heading", { name: "web-1" })).toBeInTheDocument();
  });

  it("shows a Pulse link in the header that points back to the overview", () => {
    renderAt("/hosts/web-1");

    expect(screen.getByRole("link", { name: "Pulse" })).toHaveAttribute("href", "/");
  });
});
