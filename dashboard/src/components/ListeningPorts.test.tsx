import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { LatestMetric } from "../api/types";
import { ListeningPorts } from "./ListeningPorts";

function port(portNumber: number, tags: Record<string, string> = {}): LatestMetric {
  return {
    name: "network.listening_port",
    unit: "bool",
    tags: { protocol: "tcp", address: "0.0.0.0", port: String(portNumber), ...tags },
    value: 1,
    timestamp: 100,
  };
}

function count(value: number): LatestMetric {
  return { name: "network.listening_ports", unit: "count", tags: {}, value, timestamp: 100 };
}

describe("ListeningPorts", () => {
  it("lists each open port with its protocol, address, and process", () => {
    render(<ListeningPorts metrics={[port(5432, { process: "postgres" }), port(22, { address: "::" }), count(2)]} />);

    expect(screen.getByRole("heading", { name: "Listening ports (2)" })).toBeInTheDocument();
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows.map((r) => within(r).getAllByRole("cell").map((c) => c.textContent))).toEqual([
      ["22", "TCP", "::", "—", ""],
      ["5432", "TCP", "0.0.0.0", "postgres", ""],
    ]);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("flags exposed services in the table and in a warning", () => {
    render(
      <ListeningPorts
        metrics={[
          port(22),
          port(5432, { process: "postgres", exposed: "true" }),
          port(5432, { address: "::", exposed: "true" }),
          count(3),
        ]}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent(
      "1 sensitive service is listening on an address other machines can reach: 5432/tcp (postgres).",
    );
    const flagged = screen.getAllByText("Exposed").map((badge) => badge.closest("tr"));
    expect(flagged).toHaveLength(2);
    expect(flagged.every((row) => row?.classList.contains("port-row--exposed"))).toBe(true);
  });

  it("says so when nothing is listening", () => {
    render(<ListeningPorts metrics={[count(0)]} />);

    expect(screen.getByText("Nothing is listening on this host.")).toBeInTheDocument();
  });

  it("shows an empty state before any port scan arrives", () => {
    render(<ListeningPorts metrics={[]} />);

    expect(screen.getByText("No port scan reported yet.")).toBeInTheDocument();
  });
});
