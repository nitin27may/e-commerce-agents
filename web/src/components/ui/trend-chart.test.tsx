import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { TrendChart } from "./trend-chart";

/**
 * recharts' ResponsiveContainer only renders its children once it measures
 * a non-zero size via ResizeObserver — jsdom never provides real layout, so
 * these tests cover the empty-state path (a pure conditional) and confirm
 * the non-empty path mounts the chart container without crashing, rather
 * than asserting on internal SVG contents recharts won't produce here.
 */
describe("TrendChart", () => {
  it("shows an empty-state message instead of an empty chart", () => {
    render(<TrendChart data={[]} xKey="month" series={[{ key: "rating", label: "Rating" }]} />);
    expect(screen.getByText("No trend data available.")).toBeInTheDocument();
  });

  it("mounts the chart container for non-empty data without crashing", () => {
    const { container } = render(
      <TrendChart
        data={[
          { month: "Jan", rating: 4.2 },
          { month: "Feb", rating: 4.5 },
        ]}
        xKey="month"
        series={[{ key: "rating", label: "Rating" }]}
      />
    );
    expect(container.querySelector('[data-slot="chart"]')).toBeInTheDocument();
    expect(screen.queryByText("No trend data available.")).not.toBeInTheDocument();
  });
});
