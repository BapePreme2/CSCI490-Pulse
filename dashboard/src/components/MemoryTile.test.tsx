import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { LatestMetric } from "../api/types";
import { MemoryTile } from "./MemoryTile";

function metric(name: string, value: number): LatestMetric {
  return { name, unit: "MB", tags: {}, value, timestamp: 0 };
}

describe("MemoryTile", () => {
  it("shows percent used and the raw MB/GB values", () => {
    render(<MemoryTile metrics={[metric("memory.total", 2048), metric("memory.used", 1024)]} />);

    expect(screen.getByText("50.0%")).toBeInTheDocument();
    expect(screen.getByText("1.0 GB / 2.0 GB")).toBeInTheDocument();
  });

  it("shows an empty state when memory data is missing", () => {
    render(<MemoryTile metrics={[]} />);

    expect(screen.getByText("No data yet")).toBeInTheDocument();
  });

  it("shows an empty state rather than dividing by zero when total is 0", () => {
    render(<MemoryTile metrics={[metric("memory.total", 0), metric("memory.used", 0)]} />);

    expect(screen.getByText("No data yet")).toBeInTheDocument();
  });
});
