"use client";

import { CartesianGrid, Line, LineChart, Tooltip, XAxis, YAxis } from "recharts";
import { CHART_COLORS, ChartContainer } from "@/components/ui/chart";

export interface TrendChartSeries {
  key: string;
  label: string;
  /** Index into CHART_COLORS; defaults to the series' position. */
  colorIndex?: number;
}

export interface TrendChartProps {
  data: Array<Record<string, string | number>>;
  xKey: string;
  series: TrendChartSeries[];
  height?: number;
}

const TOOLTIP_STYLE = {
  background: "var(--popover)",
  border: "1px solid var(--border)",
  borderRadius: "0.5rem",
  color: "var(--popover-foreground)",
  fontSize: "12px",
} as const;

/** Line chart for any time-series a specialist returns (e.g. monthly rating trend). */
export function TrendChart({ data, xKey, series, height = 220 }: TrendChartProps) {
  if (data.length === 0) {
    return <p className="py-4 text-center text-xs text-muted-foreground">No trend data available.</p>;
  }

  return (
    <ChartContainer height={height}>
      <LineChart data={data} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
        <XAxis
          dataKey={xKey}
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
        <Tooltip cursor={{ stroke: "var(--border)" }} contentStyle={TOOLTIP_STYLE} />
        {series.map((s, i) => (
          <Line
            key={s.key}
            type="monotone"
            dataKey={s.key}
            name={s.label}
            stroke={CHART_COLORS[(s.colorIndex ?? i) % CHART_COLORS.length]}
            strokeWidth={2}
            dot={{ r: 3 }}
          />
        ))}
      </LineChart>
    </ChartContainer>
  );
}
