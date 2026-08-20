import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { DataTable } from "./data-table";

interface Row {
  name: string;
  stock: number;
  [key: string]: unknown;
}

describe("DataTable", () => {
  const columns = [
    { key: "name", header: "Warehouse" },
    { key: "stock", header: "Stock", align: "right" as const },
  ];
  const rows: Row[] = [
    { name: "East", stock: 42 },
    { name: "West", stock: 0 },
  ];

  it("renders headers and cell values", () => {
    render(<DataTable columns={columns} rows={rows} />);
    expect(screen.getByText("Warehouse")).toBeInTheDocument();
    expect(screen.getByText("Stock")).toBeInTheDocument();
    expect(screen.getByText("East")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("West")).toBeInTheDocument();
    expect(screen.getByText("0")).toBeInTheDocument();
  });

  it("uses a custom render function when provided", () => {
    render(
      <DataTable
        columns={[
          { key: "name", header: "Warehouse" },
          { key: "stock", header: "Stock", render: (r) => (r.stock > 0 ? "In stock" : "Out of stock") },
        ]}
        rows={rows}
      />
    );
    expect(screen.getByText("In stock")).toBeInTheDocument();
    expect(screen.getByText("Out of stock")).toBeInTheDocument();
  });

  it("shows the empty message instead of an empty table", () => {
    render(<DataTable columns={columns} rows={[]} emptyMessage="Nothing here" />);
    expect(screen.getByText("Nothing here")).toBeInTheDocument();
    expect(screen.queryByText("Warehouse")).not.toBeInTheDocument();
  });

  it("renders an optional caption", () => {
    render(<DataTable columns={columns} rows={rows} caption="Regional stock" />);
    expect(screen.getByText("Regional stock")).toBeInTheDocument();
  });
});
