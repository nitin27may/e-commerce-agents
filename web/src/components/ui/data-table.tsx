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
  header: React.ReactNode;
  align?: "left" | "center" | "right";
  render?: (row: T) => React.ReactNode;
  /** Lets long text wrap; cells otherwise stay on one line. */
  wrap?: boolean;
  /** Extra classes for this column's header and cells (e.g. a width). */
  className?: string;
}

export interface DataTableProps<T extends Record<string, unknown>> {
  columns: DataTableColumn<T>[];
  rows: T[];
  caption?: string;
  emptyMessage?: string;
  /** Compact rows for tables inside a chat card: 12px text, 32px header, tighter cells. */
  dense?: boolean;
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
  dense = false,
}: DataTableProps<T>) {
  if (rows.length === 0) {
    return <p className="py-4 text-center text-xs text-muted-foreground">{emptyMessage}</p>;
  }

  return (
    <Table className={cn(dense && "text-xs")}>
      {caption && <TableCaption>{caption}</TableCaption>}
      <TableHeader>
        <TableRow>
          {columns.map((col) => (
            <TableHead
              key={col.key}
              className={cn(ALIGN_CLASS[col.align ?? "left"], dense && "h-8 px-2 text-muted-foreground first:pl-0 last:pr-0", col.className)}
            >
              {col.header}
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row, i) => (
          <TableRow key={i} className={cn(dense && "hover:bg-transparent")}>
            {columns.map((col) => (
              <TableCell
                key={col.key}
                className={cn(
                  ALIGN_CLASS[col.align ?? "left"],
                  dense && "px-2 py-1.5 first:pl-0 last:pr-0",
                  col.wrap && "whitespace-normal",
                  col.className,
                )}
              >
                {col.render ? col.render(row) : String(row[col.key] ?? "")}
              </TableCell>
            ))}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
