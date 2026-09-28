import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { App } from "./App";

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
