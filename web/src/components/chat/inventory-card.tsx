"use client";

import { useState } from "react";
import { CalendarClock, Check, Truck, Warehouse } from "lucide-react";
import { DataTable, type DataTableColumn } from "@/components/ui/data-table";
import { StatusBadge, TONE_TEXT_CLASS } from "@/components/ui/status-badge";
import { formatPrice } from "@/lib/format";
import type { InventoryData } from "@/lib/chat-schemas";
import { cn } from "@/lib/utils";
import { CardAction, CardSection, ChatCard } from "./chat-card";
import { formatCardDate, formatDeliveryWindow, humanizeEnum, shippingLabel } from "./card-format";

type WarehouseStock = NonNullable<InventoryData["warehouses"]>[number];
type RestockEntry = NonNullable<InventoryData["upcoming_restocks"]>[number];
type ShippingOption = NonNullable<InventoryData["shipping_options"]>[number];

export function StockLevel({ quantity, low }: { quantity?: number; low?: boolean }) {
  if (quantity == null) return <span className="text-muted-foreground">—</span>;
  if (quantity === 0) return <span className={cn("font-medium", TONE_TEXT_CLASS.destructive)}>Out of stock</span>;
  if (low) return <span className={cn("font-medium", TONE_TEXT_CLASS.warning)}>{quantity} (low)</span>;
  return <span>{quantity}</span>;
}

/** "East Coast Warehouse" already says "east"; a Region column beside it only repeats it. */
function regionIsRedundant(rows: WarehouseStock[]): boolean {
  return rows.every((r) => !r.region || (r.warehouse ?? "").toLowerCase().includes(r.region.toLowerCase()));
}

function warehouseColumns(rows: WarehouseStock[]): DataTableColumn<WarehouseStock>[] {
  return [
    { key: "warehouse", header: "Warehouse" },
    ...(regionIsRedundant(rows)
      ? []
      : [{ key: "region", header: "Region", render: (r: WarehouseStock) => humanizeEnum(r.region) }]),
    {
      key: "quantity",
      header: "Units",
      align: "right",
      render: (r) => <StockLevel quantity={r.quantity} low={r.low_stock} />,
    },
  ];
}

const RESTOCK_COLUMNS: DataTableColumn<RestockEntry>[] = [
  { key: "warehouse", header: "Warehouse" },
  { key: "expected_quantity", header: "Units", align: "right" },
  { key: "expected_date", header: "Expected", align: "right", render: (r) => formatCardDate(r.expected_date) },
];

function stockSummary(data: InventoryData): React.ReactNode {
  if (data.total_quantity == null) return null;
  if (data.total_quantity === 0) {
    // The header badge already says "Out of stock"; the line only earns its place without one.
    if (data.in_stock === false) return null;
    return <p className={cn("text-sm font-medium", TONE_TEXT_CLASS.destructive)}>Out of stock in every warehouse</p>;
  }
  const where = data.warehouses?.length ? ` across ${data.warehouses.length} warehouse${data.warehouses.length === 1 ? "" : "s"}` : "";
  return (
    <p className="text-sm text-foreground">
      <span className="font-semibold tabular-nums">{data.total_quantity}</span> units available{where}
    </p>
  );
}

function ShippingOptions({
  options,
  onAction,
}: {
  options: ShippingOption[];
  onAction?: (message: string) => void;
}) {
  const [chosen, setChosen] = useState<number | null>(null);
  return (
    <ul className="space-y-1.5">
      {options.map((opt, i) => {
        const isChosen = chosen === i;
        return (
          <li
            key={i}
            className={cn(
              "flex items-center justify-between gap-3 rounded-lg border px-3 py-2 text-xs transition-colors",
              isChosen ? "border-primary/50 bg-primary/5" : "border-border",
            )}
          >
            <div className="min-w-0">
              <div className="truncate font-medium text-foreground">{shippingLabel(opt.carrier, opt.speed_tier)}</div>
              {opt.delivery_window && (
                <div className="text-muted-foreground">{formatDeliveryWindow(opt.delivery_window)}</div>
              )}
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <span className="font-semibold tabular-nums text-foreground">{formatPrice(opt.price)}</span>
              {onAction && (
                <CardAction
                  icon={isChosen ? Check : undefined}
                  variant={isChosen ? "primary" : "outline"}
                  pressed={isChosen}
                  disabled={chosen !== null && !isChosen}
                  onClick={() => {
                    if (chosen !== null) return;
                    setChosen(i);
                    // Wording kept stable: it is what the assistant receives as the shopper's reply.
                    onAction(
                      `I'll go with ${opt.carrier}${opt.speed_tier ? ` (${opt.speed_tier})` : ""} shipping for ${formatPrice(opt.price)}${opt.delivery_window ? `, ${opt.delivery_window}` : ""}.`,
                    );
                  }}
                >
                  {isChosen ? "Chosen" : "Choose"}
                </CardAction>
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

interface ChatInventoryCardProps {
  data: InventoryData;
  /** Re-prompts the chat after a shipping option is chosen: there is no
   * "select a carrier" endpoint, so this follows the order card's re-prompt pattern. */
  onAction?: (message: string) => void;
}

export function ChatInventoryCard({ data, onAction }: ChatInventoryCardProps) {
  const warehouses = data.warehouses ?? [];
  const restocks = data.upcoming_restocks ?? [];
  const shipping = data.shipping_options ?? [];
  const isShippingOnly = shipping.length > 0 && warehouses.length === 0 && data.total_quantity == null;
  const title = data.product_name || (isShippingOnly ? "Shipping options" : "Stock availability");

  return (
    <ChatCard
      icon={isShippingOnly ? Truck : Warehouse}
      title={title}
      meta={
        data.in_stock != null && (
          <StatusBadge label={data.in_stock ? "In stock" : "Out of stock"} tone={data.in_stock ? "success" : "destructive"} />
        )
      }
    >
      {stockSummary(data)}

      {warehouses.length > 0 && (
        <CardSection title="By warehouse">
          <DataTable dense columns={warehouseColumns(warehouses)} rows={warehouses} />
        </CardSection>
      )}

      {restocks.length > 0 && (
        <CardSection title="Upcoming restocks" icon={CalendarClock}>
          <DataTable dense columns={RESTOCK_COLUMNS} rows={restocks} />
        </CardSection>
      )}

      {data.next_restock && restocks.length === 0 && (
        <p className="text-xs text-muted-foreground">Next restock: {formatCardDate(data.next_restock)}</p>
      )}

      {shipping.length > 0 &&
        (isShippingOnly && !data.ships_from?.warehouse ? (
          // The header already says "Shipping options"; a caption would only repeat it.
          <ShippingOptions options={shipping} onAction={onAction} />
        ) : (
          <CardSection
            title={
              isShippingOnly
                ? `Ships from ${data.ships_from!.warehouse}`
                : `Shipping options${data.ships_from?.warehouse ? `, ships from ${data.ships_from.warehouse}` : ""}`
            }
            icon={isShippingOnly ? undefined : Truck}
          >
            <ShippingOptions options={shipping} onAction={onAction} />
          </CardSection>
        ))}
    </ChatCard>
  );
}
