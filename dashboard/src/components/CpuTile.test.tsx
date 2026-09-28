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

  it("shows an empty state when there is no overall reading", () => {
    render(<CpuTile metrics={[]} />);

    expect(screen.getByText("No data yet")).toBeInTheDocument();
  });
});
