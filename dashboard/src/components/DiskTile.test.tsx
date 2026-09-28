import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { LatestMetric } from "../api/types";
import { DiskTile } from "./DiskTile";

function usage(mount: string, value: number): LatestMetric {
  return { name: "disk.usage", unit: "percent", tags: { mount, device: "/dev/sda" }, value, timestamp: 0 };
}

function free(mount: string, value: number): LatestMetric {
  return { name: "disk.free", unit: "GB", tags: { mount, device: "/dev/sda" }, value, timestamp: 0 };
}

describe("DiskTile", () => {
  it("lists each mount fullest-first, with its free space", () => {
    render(<DiskTile metrics={[usage("/", 10), usage("/data", 90), free("/", 100), free("/data", 5)]} />);

    const items = screen.getAllByRole("listitem").map((li) => li.textContent);
    expect(items).toEqual(["/data: 90.0% (5.0 GB free)", "/: 10.0% (100.0 GB free)"]);
  });

  it("shows an empty state when there is no disk data", () => {
    render(<DiskTile metrics={[]} />);

    expect(screen.getByText("No data yet")).toBeInTheDocument();
  });
});
