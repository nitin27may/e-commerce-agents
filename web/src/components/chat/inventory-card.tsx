"use client";

import { Truck, Warehouse } from "lucide-react";
import { Button } from "@/components/ui/button";
import { DataTable, type DataTableColumn } from "@/components/ui/data-table";
import { StatusBadge } from "@/components/ui/status-badge";
import { StatTile } from "@/components/ui/stat-tile";
import { formatPrice } from "@/lib/format";

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

interface ShipsFrom {
  warehouse?: string;
  region?: string;
  quantity_available?: number;
}

interface ShippingOption {
  carrier: string;
  speed_tier?: string;
  price: number;
  delivery_window?: string;
}

interface InventoryData {
  product_id?: string;
  product_name?: string;
  in_stock?: boolean;
  total_quantity?: number;
  warehouses?: WarehouseStock[];
  upcoming_restocks?: RestockEntry[];
  next_restock?: string;
  ships_from?: ShipsFrom;
  shipping_options?: ShippingOption[];
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

interface ChatInventoryCardProps {
  data: InventoryData;
  /** Re-prompts the chat, e.g. after picking a shipping option — there's no
   * direct-mutation endpoint for "select a carrier" the way cart add-item
   * has one, so this follows the existing Track/Cancel/Return re-prompt
   * pattern (order-card.tsx) rather than inventing a third mechanism. */
  onAction?: (message: string) => void;
}

export function ChatInventoryCard({ data, onAction }: ChatInventoryCardProps) {
  // Nothing to show — e.g. a tool call resolved no data and the model
  // still emitted an all-empty fence. Don't render a header with a
  // blank body underneath it. product_name alone still counts: it
  // identifies which product this is about.
  const hasAnyData =
    data.product_name != null ||
    data.in_stock != null ||
    data.total_quantity != null ||
    (data.warehouses && data.warehouses.length > 0) ||
    (data.upcoming_restocks && data.upcoming_restocks.length > 0) ||
    data.next_restock != null ||
    (data.shipping_options && data.shipping_options.length > 0);
  if (!hasAnyData) return null;

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

        {data.shipping_options && data.shipping_options.length > 0 && (
          <div>
            <p className="flex items-center gap-1 text-[11px] font-medium text-muted-foreground mb-1">
              <Truck className="size-3" />
              Shipping options
              {data.ships_from?.warehouse && ` — ships from ${data.ships_from.warehouse}`}
            </p>
            <div className="space-y-1.5">
              {data.shipping_options.map((opt, i) => (
                <div
                  key={i}
                  className="flex items-center justify-between gap-2 rounded-lg border border-border px-3 py-2 text-xs"
                >
                  <div className="min-w-0">
                    <div className="font-medium text-foreground truncate">
                      {opt.carrier}
                      {opt.speed_tier && <span className="text-muted-foreground"> · {opt.speed_tier}</span>}
                    </div>
                    {opt.delivery_window && (
                      <div className="text-[10px] text-muted-foreground">{opt.delivery_window}</div>
                    )}
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <span className="font-semibold text-foreground">{formatPrice(opt.price)}</span>
                    {onAction && (
                      <Button
                        size="sm"
                        variant="outline"
                        className="h-6 text-[11px] px-2"
                        onClick={() =>
                          onAction(
                            `I'll go with ${opt.carrier}${opt.speed_tier ? ` (${opt.speed_tier})` : ""} shipping for ${formatPrice(opt.price)}${opt.delivery_window ? `, ${opt.delivery_window}` : ""}.`
                          )
                        }
                      >
                        Select
                      </Button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
