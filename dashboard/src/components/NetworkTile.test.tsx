import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { LatestMetric } from "../api/types";
import { NetworkTile } from "./NetworkTile";

function rate(name: string, iface: string, value: number): LatestMetric {
  return { name, unit: "MB/s", tags: { interface: iface }, value, timestamp: 0 };
}

describe("NetworkTile", () => {
  it("lists each interface with up/down rates", () => {
    render(
      <NetworkTile
        metrics={[rate("network.bytes_sent", "eth0", 1.5), rate("network.bytes_recv", "eth0", 2.5)]}
      />,
    );

    expect(screen.getByText("eth0: 1.50 MB/s up / 2.50 MB/s down")).toBeInTheDocument();
  });

  it("defaults a missing direction to zero", () => {
    render(<NetworkTile metrics={[rate("network.bytes_sent", "eth0", 1)]} />);

    expect(screen.getByText("eth0: 1.00 MB/s up / 0.00 MB/s down")).toBeInTheDocument();
  });

  it("shows an empty state when there is no network data", () => {
    render(<NetworkTile metrics={[]} />);

    expect(screen.getByText("No data yet")).toBeInTheDocument();
  });
});
