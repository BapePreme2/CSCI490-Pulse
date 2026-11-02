import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Anomaly, AnomalySeries } from "../api/types";
import { formatWhen } from "../metrics/anomalies";
import { AnomalyList, MAX_LISTED_ANOMALIES } from "./AnomalyList";

function run(start: number, values: number[], direction: Anomaly["direction"] = "above"): Anomaly {
  const points = values.map((value, i) => ({
    timestamp: start + i * 10,
    value,
    mean: 12,
    spread: 2,
    deviation: (value - 12) / 2,
  }));
  return { start, end: start + (values.length - 1) * 10, direction, points };
}

function renderList(anomalies: AnomalySeries[]) {
  return render(
    <AnomalyList anomalies={anomalies} sigma={3} windowSeconds={3600} rangeSeconds={3600} relevantKeys={["core"]} />,
  );
}

describe("AnomalyList", () => {
  it("says what counts as an anomaly", () => {
    renderList([]);

    expect(screen.getByText(/samples 3σ or more from their baseline, the 1h before each one/)).toBeInTheDocument();
  });

  it("says when there are none", () => {
    renderList([]);

    expect(screen.getByText("None in this range.")).toBeInTheDocument();
  });

  it("describes an event: when, which series, how many samples, and the furthest off", () => {
    const start = 1_800_000_000;
    renderList([{ unit: "percent", tags: { core: "2" }, anomalies: [run(start, [40, 60, 50])] }]);

    const [item] = within(screen.getByRole("list", { name: "Anomalies" })).getAllByRole("listitem");
    expect(item).toHaveTextContent(`${formatWhen(start, 3600)}–${formatWhen(start + 20, 3600)}`);
    expect(item).toHaveTextContent("core 2");
    expect(item).toHaveTextContent("above baseline (3 samples), furthest off: 60%, +24.0σ from 12 ± 2%");
  });

  it("names the series furthest off when an event spans several", () => {
    const start = 1_800_000_000;
    renderList([
      { unit: "percent", tags: {}, anomalies: [run(start + 0.001, [30, 32])] },
      { unit: "percent", tags: { core: "0" }, anomalies: [run(start, [90, 92])] },
    ]);

    const [item] = within(screen.getByRole("list", { name: "Anomalies" })).getAllByRole("listitem");
    expect(item).toHaveTextContent("overall, core 0");
    expect(item).toHaveTextContent("above baseline (4 samples), furthest off: core 0 at 92%, +40.0σ from 12 ± 2%");
  });

  it("gives a single sample one time and its value", () => {
    const start = 1_800_000_000;
    renderList([{ unit: "percent", tags: {}, anomalies: [run(start, [2], "below")] }]);

    const [item] = within(screen.getByRole("list", { name: "Anomalies" })).getAllByRole("listitem");
    expect(item).toHaveTextContent(`${formatWhen(start, 3600)}overallbelow baseline (1 sample): 2%, −5.0σ from 12 ± 2%`);
    expect(item).not.toHaveTextContent("–");
  });

  it("shows only the most recent events when there are many", () => {
    const runs = Array.from({ length: MAX_LISTED_ANOMALIES + 3 }, (_, i) => run(1_800_000_000 + i * 100, [60]));
    renderList([{ unit: "percent", tags: { core: "0" }, anomalies: runs }]);

    expect(within(screen.getByRole("list", { name: "Anomalies" })).getAllByRole("listitem")).toHaveLength(
      MAX_LISTED_ANOMALIES,
    );
    expect(
      screen.getByText(`Showing the ${MAX_LISTED_ANOMALIES} most recent of ${MAX_LISTED_ANOMALIES + 3}.`),
    ).toBeInTheDocument();
  });
});
