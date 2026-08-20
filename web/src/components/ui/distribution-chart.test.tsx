import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { DistributionChart } from "./distribution-chart";

/** See trend-chart.test.tsx for why this doesn't assert on recharts internals. */
describe("DistributionChart", () => {
  it("shows an empty-state message instead of an empty chart", () => {
    render(<DistributionChart data={[]} />);
    expect(screen.getByText("No distribution data available.")).toBeInTheDocument();
  });

  it("mounts the chart container for non-empty data without crashing", () => {
    const { container } = render(
      <DistributionChart
        data={[
          { label: "5 star", value: 12 },
          { label: "4 star", value: 8 },
        ]}
      />
    );
    expect(container.querySelector('[data-slot="chart"]')).toBeInTheDocument();
    expect(screen.queryByText("No distribution data available.")).not.toBeInTheDocument();
  });
});
