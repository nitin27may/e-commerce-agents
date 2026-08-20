"use client";

import { Tag, Ticket } from "lucide-react";
import { DataTable, type DataTableColumn } from "@/components/ui/data-table";
import { formatPrice } from "@/lib/format";

interface SavingsLine {
  type: "coupon" | "bundle_promotion" | "buy_x_get_y" | "flash_sale" | "loyalty_discount";
  code?: string;
  name?: string;
  description?: string;
  tier?: string;
  product?: string;
  amount: number;
}

interface DealCoupon {
  code: string;
  description?: string;
  discount_type?: "percentage" | "fixed";
  discount_value?: number;
  min_spend?: number;
  valid_until?: string;
  [key: string]: unknown;
}

interface DealPromotion {
  name: string;
  type?: string;
  end_date?: string;
  [key: string]: unknown;
}

interface PricingData {
  original_total?: number;
  savings?: SavingsLine[];
  total_savings?: number;
  final_total?: number;
  savings_percentage?: number;
  coupons?: DealCoupon[];
  promotions?: DealPromotion[];
}

function savingsLabel(line: SavingsLine): string {
  switch (line.type) {
    case "coupon":
      return line.code ? `Coupon ${line.code}` : "Coupon";
    case "loyalty_discount":
      return line.tier ? `${line.tier[0].toUpperCase()}${line.tier.slice(1)} loyalty discount` : "Loyalty discount";
    case "bundle_promotion":
      return line.name || "Bundle deal";
    case "buy_x_get_y":
      return line.name ? `${line.name}${line.product ? ` (${line.product})` : ""}` : "Buy X get Y";
    case "flash_sale":
      return line.name ? `${line.name}${line.product ? ` (${line.product})` : ""}` : "Flash sale";
    default:
      return line.description || "Savings";
  }
}

const COUPON_COLUMNS: DataTableColumn<DealCoupon>[] = [
  { key: "code", header: "Code" },
  {
    key: "description",
    header: "Details",
    // Long descriptions push the Discount column out of the card's
    // max-w-md — table cells default to whitespace-nowrap (ui/table.tsx),
    // so without a width cap the row just grows instead of wrapping.
    // Found live: WELCOME10's full description hid its own discount %.
    render: (r) => (
      <span className="block max-w-[140px] truncate" title={r.description}>
        {r.description ?? "—"}
      </span>
    ),
  },
  {
    key: "discount_value",
    header: "Discount",
    align: "right",
    render: (r) =>
      r.discount_value == null
        ? "—"
        : r.discount_type === "fixed"
          ? formatPrice(r.discount_value)
          : `${r.discount_value}%`,
  },
];

const PROMOTION_COLUMNS: DataTableColumn<DealPromotion>[] = [
  { key: "name", header: "Promotion" },
  { key: "type", header: "Type" },
  { key: "end_date", header: "Ends" },
];

export function ChatPricingCard({ data }: { data: PricingData }) {
  const hasWaterfall = data.original_total != null && data.savings && data.savings.length > 0;
  const hasCoupons = data.coupons && data.coupons.length > 0;
  const hasPromotions = data.promotions && data.promotions.length > 0;

  // Nothing to show — e.g. optimize_cart couldn't resolve a cart and the
  // model still emitted an all-empty fence. Don't render a header with a
  // blank body underneath it.
  if (!hasWaterfall && !hasCoupons && !hasPromotions) return null;

  return (
    <div className="my-2 max-w-md rounded-xl border border-border bg-card shadow-sm overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 border-b border-border bg-muted px-4 py-2.5">
        <Tag className="size-4 text-muted-foreground shrink-0" />
        <span className="text-sm font-medium text-foreground">
          {hasWaterfall ? "Savings Breakdown" : "Deals & Promotions"}
        </span>
      </div>

      <div className="p-4 space-y-3">
        {/* Discount waterfall */}
        {hasWaterfall && (
          <div className="text-sm space-y-1.5">
            <div className="flex items-center justify-between text-muted-foreground">
              <span>Original total</span>
              <span>{formatPrice(data.original_total!)}</span>
            </div>
            {data.savings!.map((line, i) => (
              <div key={i} className="flex items-center justify-between text-success">
                <span>{savingsLabel(line)}</span>
                <span>-{formatPrice(line.amount)}</span>
              </div>
            ))}
            <div className="border-t border-border pt-1.5 flex items-center justify-between font-semibold text-foreground">
              <span>
                Final total
                {data.savings_percentage != null && (
                  <span className="ml-1 text-xs font-normal text-success">
                    (saved {data.savings_percentage}%)
                  </span>
                )}
              </span>
              <span>{formatPrice(data.final_total ?? data.original_total! - (data.total_savings ?? 0))}</span>
            </div>
          </div>
        )}

        {/* Active coupons */}
        {hasCoupons && (
          <div>
            <p className="flex items-center gap-1 text-[11px] font-medium text-muted-foreground mb-1">
              <Ticket className="size-3" /> Active coupons
            </p>
            <DataTable columns={COUPON_COLUMNS} rows={data.coupons!} />
          </div>
        )}

        {/* Active promotions */}
        {hasPromotions && (
          <div>
            <p className="text-[11px] font-medium text-muted-foreground mb-1">Active promotions</p>
            <DataTable columns={PROMOTION_COLUMNS} rows={data.promotions!} />
          </div>
        )}
      </div>
    </div>
  );
}
