import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TimeRangeSelector } from "./TimeRangeSelector";

const OPTIONS = [
  { label: "1h", seconds: 3600 },
  { label: "6h", seconds: 21600 },
];

describe("TimeRangeSelector", () => {
  it("renders one button per option", () => {
    render(<TimeRangeSelector options={OPTIONS} selected={OPTIONS[0]} onSelect={vi.fn()} />);

    expect(screen.getByRole("button", { name: "1h" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "6h" })).toBeInTheDocument();
  });

  it("marks only the selected option as pressed", () => {
    render(<TimeRangeSelector options={OPTIONS} selected={OPTIONS[1]} onSelect={vi.fn()} />);

    expect(screen.getByRole("button", { name: "1h" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("button", { name: "6h" })).toHaveAttribute("aria-pressed", "true");
  });

  it("calls onSelect with the clicked option", async () => {
    const onSelect = vi.fn();
    render(<TimeRangeSelector options={OPTIONS} selected={OPTIONS[0]} onSelect={onSelect} />);

    screen.getByRole("button", { name: "6h" }).click();

    expect(onSelect).toHaveBeenCalledWith(OPTIONS[1]);
  });
});
