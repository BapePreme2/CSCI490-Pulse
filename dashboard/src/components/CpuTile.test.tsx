import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { LatestMetric } from "../api/types";
import { CpuTile } from "./CpuTile";

function metric(tags: Record<string, string>, value: number): LatestMetric {
  return { name: "cpu.usage", unit: "percent", tags, value, timestamp: 0 };
}

describe("CpuTile", () => {
  it("shows the overall usage and each core in order", () => {
    render(
      <CpuTile metrics={[metric({}, 42.5), metric({ core: "1" }, 10), metric({ core: "0" }, 5)]} />,
    );

    expect(screen.getByText("42.5%")).toBeInTheDocument();
    const items = screen.getAllByRole("listitem").map((li) => li.textContent);
    expect(items).toEqual(["core 0: 5.0%", "core 1: 10.0%"]);
  });

  it("still finds the overall reading when every metric carries an unrelated tag", () => {
    // Regression: an agent config with any custom tag (e.g. environment)
    // attaches it to every metric, including the "overall" one -- that
    // must not be mistaken for a per-core reading.
    render(
      <CpuTile
        metrics={[
          metric({ environment: "demo" }, 42.5),
          metric({ core: "0", environment: "demo" }, 5),
        ]}
      />,
    );

    expect(screen.getByText("42.5%")).toBeInTheDocument();
  });

  it("shows an empty state when there is no overall reading", () => {
    render(<CpuTile metrics={[]} />);

    expect(screen.getByText("No data yet")).toBeInTheDocument();
  });
});
