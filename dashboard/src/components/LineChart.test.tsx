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

  it("draws a marker on its series' line for each marked point, in the series' color", () => {
    render(
      <LineChart
        series={[
          { label: "core 0", points: [{ timestamp: 0, value: 10 }, { timestamp: 10, value: 90 }] },
          {
            label: "core 1",
            points: [{ timestamp: 0, value: 5 }, { timestamp: 10, value: 6 }],
            markers: [{ timestamp: 10, value: 6, label: "core 1 at 10: +4.0σ" }],
          },
        ]}
        markerLabel="anomaly"
      />,
    );

    const [marker] = screen.getAllByTestId("marker-core 1");
    const dot = marker.querySelector(".line-chart-marker-dot")!;
    const [, lastPoint] = screen.getByTestId("line-core 1").getAttribute("points")!.trim().split(/\s+/);
    expect(`${dot.getAttribute("cx")},${dot.getAttribute("cy")}`).toBe(lastPoint);
    expect(dot.getAttribute("fill")).toBe(screen.getByTestId("line-core 1").getAttribute("stroke"));
    expect(marker.querySelector("title")?.textContent).toBe("core 1 at 10: +4.0σ");
    expect(screen.queryAllByTestId("marker-core 0")).toHaveLength(0);
  });

  it("names markers in the legend and the chart's title only when there are some", () => {
    const series = [{ label: "core 0", points: [{ timestamp: 0, value: 1 }, { timestamp: 10, value: 50 }] }];
    const without = render(<LineChart series={series} markerLabel="anomaly" />);
    expect(without.container.querySelector(".line-chart-legend")).toBeNull();
    without.unmount();

    const { container } = render(
      <LineChart
        series={[{ ...series[0], markers: [{ timestamp: 10, value: 50, label: "spike" }] }]}
        markerLabel="anomaly"
      />,
    );

    // One series: no swatch for it, just the marker key.
    expect(container.querySelectorAll(".line-chart-legend li")).toHaveLength(1);
    expect(screen.getByText("anomaly")).toBeInTheDocument();
    expect(screen.getByRole("img")).toHaveAccessibleName("core 0 over time, 1 point marked as anomaly");
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
