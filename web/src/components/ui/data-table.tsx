"use client";

import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";

export interface DataTableColumn<T> {
  key: string;
  header: string;
  align?: "left" | "center" | "right";
  render?: (row: T) => React.ReactNode;
}

export interface DataTableProps<T extends Record<string, unknown>> {
  columns: DataTableColumn<T>[];
  rows: T[];
  caption?: string;
  emptyMessage?: string;
}

const ALIGN_CLASS: Record<NonNullable<DataTableColumn<never>["align"]>, string> = {
  left: "text-left",
  center: "text-center",
  right: "text-right",
};

/**
 * Generic table for any homogeneous list a specialist returns (warehouses,
 * deals, tier comparisons, price-history points) — the fence dispatcher
 * supplies the column definitions per data shape; this only renders them.
 */
export function DataTable<T extends Record<string, unknown>>({
  columns,
  rows,
  caption,
  emptyMessage = "No data available.",
}: DataTableProps<T>) {
  if (rows.length === 0) {
    return <p className="py-4 text-center text-xs text-muted-foreground">{emptyMessage}</p>;
  }

  return (
    <Table>
      {caption && <TableCaption>{caption}</TableCaption>}
      <TableHeader>
        <TableRow>
          {columns.map((col) => (
            <TableHead key={col.key} className={cn(ALIGN_CLASS[col.align ?? "left"])}>
              {col.header}
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row, i) => (
          <TableRow key={i}>
            {columns.map((col) => (
              <TableCell key={col.key} className={cn(ALIGN_CLASS[col.align ?? "left"])}>
                {col.render ? col.render(row) : String(row[col.key] ?? "")}
              </TableCell>
            ))}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
