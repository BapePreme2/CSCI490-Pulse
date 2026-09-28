import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LineChart } from "./LineChart";

describe("LineChart", () => {
  it("shows an empty state when every series has no points", () => {
    render(<LineChart series={[{ label: "core 0", points: [] }]} />);

    expect(screen.getByText("No data yet")).toBeInTheDocument();
  });

  it("shows an empty state when there are no series at all", () => {
    render(<LineChart series={[]} />);

    expect(screen.getByText("No data yet")).toBeInTheDocument();
  });

  it("draws one polyline per series with a point per sample", () => {
    const { container } = render(
      <LineChart
        series={[
          {
            label: "core 0",
            points: [
              { timestamp: 100, value: 10 },
              { timestamp: 200, value: 20 },
            ],
          },
          { label: "core 1", points: [{ timestamp: 100, value: 5 }] },
        ]}
      />,
    );

    const line0 = screen.getByTestId("line-core 0");
    const line1 = screen.getByTestId("line-core 1");
    expect(line0.getAttribute("points")?.trim().split(/\s+/)).toHaveLength(2);
    expect(line1.getAttribute("points")?.trim().split(/\s+/)).toHaveLength(1);
    expect(container.querySelectorAll("polyline")).toHaveLength(2);
  });

  it("sorts points by time before drawing, regardless of input order", () => {
    render(
      <LineChart
        series={[
          {
            label: "core 0",
            points: [
              { timestamp: 200, value: 20 },
              { timestamp: 100, value: 10 },
            ],
          },
        ]}
      />,
    );

    const [firstPoint] = screen.getByTestId("line-core 0").getAttribute("points")!.trim().split(/\s+/);
    // The earlier timestamp (100) must plot to a smaller x than the later one (200).
    const laterX = Number(
      screen.getByTestId("line-core 0").getAttribute("points")!.trim().split(/\s+/)[1].split(",")[0],
    );
    const firstX = Number(firstPoint.split(",")[0]);
    expect(firstX).toBeLessThan(laterX);
  });

  it("shows a legend only when there is more than one series", () => {
    const single = render(<LineChart series={[{ label: "core 0", points: [{ timestamp: 0, value: 1 }] }]} />);
    expect(single.container.querySelector(".line-chart-legend")).toBeNull();
    single.unmount();

    render(
      <LineChart
        series={[
          { label: "core 0", points: [{ timestamp: 0, value: 1 }] },
          { label: "core 1", points: [{ timestamp: 0, value: 2 }] },
        ]}
      />,
    );
    expect(screen.getByText("core 0")).toBeInTheDocument();
    expect(screen.getByText("core 1")).toBeInTheDocument();
  });

  it("labels the y-axis with the min and max value", () => {
    render(<LineChart series={[{ label: "core 0", points: [{ timestamp: 0, value: 42 }] }]} />);

    expect(screen.getByText("42.0")).toBeInTheDocument();
    expect(screen.getByText("0.0")).toBeInTheDocument();
  });

  it("does not divide by zero when every point shares one timestamp", () => {
    render(
      <LineChart
        series={[
          {
            label: "core 0",
            points: [
              { timestamp: 100, value: 1 },
              { timestamp: 100, value: 2 },
            ],
          },
        ]}
      />,
    );

    const points = screen.getByTestId("line-core 0").getAttribute("points")!.trim().split(/\s+/);
    expect(points).toHaveLength(2);
    expect(Number.isFinite(Number(points[0].split(",")[0]))).toBe(true);
  });
});
