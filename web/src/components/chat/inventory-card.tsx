"use client";

import { Warehouse } from "lucide-react";
import { DataTable, type DataTableColumn } from "@/components/ui/data-table";
import { StatusBadge } from "@/components/ui/status-badge";
import { StatTile } from "@/components/ui/stat-tile";

interface WarehouseStock {
  warehouse?: string;
  region?: string;
  quantity?: number;
  low_stock?: boolean;
  [key: string]: unknown;
}

interface RestockEntry {
  warehouse?: string;
  region?: string;
  expected_quantity?: number;
  expected_date?: string;
  [key: string]: unknown;
}

interface InventoryData {
  product_id?: string;
  product_name?: string;
  in_stock?: boolean;
  total_quantity?: number;
  warehouses?: WarehouseStock[];
  upcoming_restocks?: RestockEntry[];
  next_restock?: string;
}

const WAREHOUSE_COLUMNS: DataTableColumn<WarehouseStock>[] = [
  { key: "warehouse", header: "Warehouse" },
  { key: "region", header: "Region" },
  {
    key: "quantity",
    header: "Qty",
    align: "right",
    render: (r) => (
      <span className={r.low_stock ? "text-warning font-medium" : undefined}>
        {r.quantity ?? "—"}
        {r.low_stock ? " (low)" : ""}
      </span>
    ),
  },
];

const RESTOCK_COLUMNS: DataTableColumn<RestockEntry>[] = [
  { key: "warehouse", header: "Warehouse" },
  { key: "expected_quantity", header: "Qty", align: "right" },
  { key: "expected_date", header: "Expected" },
];

export function ChatInventoryCard({ data }: { data: InventoryData }) {
  return (
    <div className="my-2 max-w-md rounded-xl border border-border bg-card shadow-sm overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between gap-3 border-b border-border bg-muted px-4 py-2.5">
        <div className="flex items-center gap-2 min-w-0">
          <Warehouse className="size-4 text-muted-foreground shrink-0" />
          <span className="text-sm font-medium text-foreground truncate">
            {data.product_name || "Stock & Fulfillment"}
          </span>
        </div>
        {data.in_stock != null && (
          <StatusBadge
            label={data.in_stock ? "In Stock" : "Out of Stock"}
            tone={data.in_stock ? "success" : "destructive"}
          />
        )}
      </div>

      <div className="p-4 space-y-3">
        {data.total_quantity != null && (
          <StatTile
            label="Total units"
            value={data.total_quantity}
            tone={data.total_quantity > 0 ? "success" : "destructive"}
          />
        )}

        {data.warehouses && data.warehouses.length > 0 && (
          <div>
            <p className="text-[11px] font-medium text-muted-foreground mb-1">By warehouse</p>
            <DataTable columns={WAREHOUSE_COLUMNS} rows={data.warehouses} />
          </div>
        )}

        {data.upcoming_restocks && data.upcoming_restocks.length > 0 && (
          <div>
            <p className="text-[11px] font-medium text-muted-foreground mb-1">Upcoming restocks</p>
            <DataTable columns={RESTOCK_COLUMNS} rows={data.upcoming_restocks} />
          </div>
        )}

        {data.next_restock && (!data.upcoming_restocks || data.upcoming_restocks.length === 0) && (
          <p className="text-[11px] text-muted-foreground">Next restock: {data.next_restock}</p>
        )}
      </div>
    </div>
  );
}
