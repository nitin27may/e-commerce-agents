import { describe, expect, it } from "vitest";
import {
  DataTableDataSchema,
  DistributionChartDataSchema,
  StatTileDataSchema,
  TrendChartDataSchema,
} from "./chat-schemas";

describe("generative-UI primitive schemas (Phase 8.4 Stage 3)", () => {
  it("DataTableDataSchema accepts a well-shaped table", () => {
    const result = DataTableDataSchema.safeParse({
      title: "Warehouse stock",
      columns: [
        { key: "name", header: "Warehouse" },
        { key: "stock", header: "Stock", align: "right" },
      ],
      rows: [{ name: "East", stock: 42 }],
    });
    expect(result.success).toBe(true);
  });

  it("DataTableDataSchema rejects more than 50 rows", () => {
    const rows = Array.from({ length: 51 }, (_, i) => ({ name: `Row ${i}`, stock: i }));
    const result = DataTableDataSchema.safeParse({
      columns: [{ key: "name", header: "Name" }],
      rows,
    });
    expect(result.success).toBe(false);
  });

  it("TrendChartDataSchema accepts a time series", () => {
    const result = TrendChartDataSchema.safeParse({
      xKey: "month",
      series: [{ key: "rating", label: "Average rating" }],
      data: [
        { month: "Jan", rating: 4.2 },
        { month: "Feb", rating: 4.5 },
      ],
    });
    expect(result.success).toBe(true);
  });

  it("DistributionChartDataSchema accepts a label/value distribution", () => {
    const result = DistributionChartDataSchema.safeParse({
      data: [
        { label: "5 star", value: 12 },
        { label: "4 star", value: 8 },
      ],
    });
    expect(result.success).toBe(true);
  });

  it("StatTileDataSchema accepts a scalar with a tone", () => {
    const result = StatTileDataSchema.safeParse({
      label: "Risk Level",
      value: "High",
      tone: "destructive",
    });
    expect(result.success).toBe(true);
  });

  it("StatTileDataSchema rejects an unrecognized tone", () => {
    const result = StatTileDataSchema.safeParse({
      label: "Risk Level",
      value: "High",
      tone: "extreme",
    });
    expect(result.success).toBe(false);
  });
});
