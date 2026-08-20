"use client";

import { Bar, BarChart, CartesianGrid, Tooltip, XAxis, YAxis } from "recharts";
import { CHART_COLORS, ChartContainer } from "@/components/ui/chart";

export interface DistributionChartDatum {
  label: string;
  value: number;
}

export interface DistributionChartProps {
  data: DistributionChartDatum[];
  height?: number;
  colorIndex?: number;
  valueLabel?: string;
}

const TOOLTIP_STYLE = {
  background: "var(--popover)",
  border: "1px solid var(--border)",
  borderRadius: "0.5rem",
  color: "var(--popover-foreground)",
  fontSize: "12px",
} as const;

/** Bar chart for any {label, value} distribution a specialist returns (e.g. rating breakdown). */
export function DistributionChart({
  data,
  height = 200,
  colorIndex = 0,
  valueLabel = "Count",
}: DistributionChartProps) {
  if (data.length === 0) {
    return <p className="py-4 text-center text-xs text-muted-foreground">No distribution data available.</p>;
  }

  return (
    <ChartContainer height={height}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
        <XAxis
          dataKey="label"
          tick={{ fill: "var(--muted-foreground)", fontSize: 11 }}
          tickLine={false}
          axisLine={false}
        />
        <YAxis
          tick={{ fill: "var(--muted-foreground)", fontSize: 11 }}
          tickLine={false}
          axisLine={false}
          allowDecimals={false}
        />
        <Tooltip cursor={{ fill: "var(--accent)", opacity: 0.4 }} contentStyle={TOOLTIP_STYLE} />
        <Bar
          dataKey="value"
          name={valueLabel}
          fill={CHART_COLORS[colorIndex % CHART_COLORS.length]}
          radius={[4, 4, 0, 0]}
          maxBarSize={48}
        />
      </BarChart>
    </ChartContainer>
  );
}
