"use client";

import { Megaphone, Tag, Ticket } from "lucide-react";
import { DataTable, type DataTableColumn } from "@/components/ui/data-table";
import { TONE_TEXT_CLASS } from "@/components/ui/status-badge";
import { formatPrice } from "@/lib/format";
import type { PricingData } from "@/lib/chat-schemas";
import { CardAction, CardSection, ChatCard } from "./chat-card";
import { formatCardDate, humanizeEnum } from "./card-format";

type SavingsLine = NonNullable<PricingData["savings"]>[number];
type DealCoupon = NonNullable<PricingData["coupons"]>[number];
type DealPromotion = NonNullable<PricingData["promotions"]>[number];

function withProduct(name: string, product?: string): string {
  return product ? `${name} (${product})` : name;
}

export function savingsLabel(line: SavingsLine): string {
  switch (line.type) {
    case "coupon":
      return line.code ? `Coupon ${line.code}` : "Coupon";
    case "loyalty_discount":
      return line.tier ? `${humanizeEnum(line.tier)} loyalty discount` : "Loyalty discount";
    case "bundle_promotion":
      return line.name || "Bundle deal";
    case "buy_x_get_y":
      return line.name ? withProduct(line.name, line.product) : "Buy X, get Y";
    case "flash_sale":
      return line.name ? withProduct(line.name, line.product) : "Flash sale";
    default:
      return line.description || "Savings";
  }
}

function couponDiscount(c: DealCoupon): string | null {
  if (c.discount_value == null) return null;
  return c.discount_type === "fixed" ? formatPrice(c.discount_value) : `${c.discount_value}%`;
}

/** get_active_deals nests the percentage under `rules`; read it without widening the schema. */
function promotionDiscount(p: DealPromotion): string | null {
  const rules = p.rules as { discount_pct?: unknown } | undefined;
  const pct = rules && typeof rules === "object" ? rules.discount_pct : undefined;
  return typeof pct === "number" ? `${pct}%` : null;
}

/** Only the columns at least one row has a value for: a column of dashes says nothing. */
function presentColumns<T extends Record<string, unknown>>(
  rows: T[],
  columns: Array<DataTableColumn<T> & { value?: (row: T) => unknown }>,
): DataTableColumn<T>[] {
  return columns.filter((col) => !col.value || rows.some((r) => col.value!(r) != null && col.value!(r) !== ""));
}

function couponColumns(coupons: DealCoupon[], onAction?: (message: string) => void): DataTableColumn<DealCoupon>[] {
  return presentColumns(coupons, [
    {
      key: "code",
      header: "Code",
      render: (r) => <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs text-foreground">{r.code}</code>,
    },
    { key: "description", header: "Details", wrap: true, value: (r) => r.description, render: (r) => r.description ?? "" },
    { key: "discount_value", header: "Discount", align: "right", value: couponDiscount, render: (r) => couponDiscount(r) ?? "" },
    { key: "valid_until", header: "Ends", value: (r) => r.valid_until, render: (r) => formatCardDate(r.valid_until) },
    ...(onAction
      ? [
          {
            key: "apply",
            header: <span className="sr-only">Apply</span>,
            align: "right" as const,
            render: (r: DealCoupon) => (
              <CardAction onClick={() => onAction(`Apply coupon ${r.code} to my cart`)} className="h-6 px-2">
                Apply
              </CardAction>
            ),
          },
        ]
      : []),
  ]);
}

function promotionColumns(promotions: DealPromotion[]): DataTableColumn<DealPromotion>[] {
  return presentColumns(promotions, [
    { key: "name", header: "Promotion", wrap: true },
    { key: "type", header: "Type", value: (r) => r.type, render: (r) => humanizeEnum(r.type) },
    { key: "discount", header: "Discount", align: "right", value: promotionDiscount, render: (r) => promotionDiscount(r) ?? "" },
    { key: "end_date", header: "Ends", value: (r) => r.end_date, render: (r) => formatCardDate(r.end_date) },
  ]);
}

interface ChatPricingCardProps {
  data: PricingData;
  /** Sends "Apply coupon CODE to my cart" for a coupon's Apply action. */
  onAction?: (message: string) => void;
}

export function ChatPricingCard({ data, onAction }: ChatPricingCardProps) {
  const savings = data.savings ?? [];
  const coupons = data.coupons ?? [];
  const promotions = data.promotions ?? [];
  const hasWaterfall = data.original_total != null && savings.length > 0;
  if (!hasWaterfall && coupons.length === 0 && promotions.length === 0) return null;

  const finalTotal = data.final_total ?? (data.original_total ?? 0) - (data.total_savings ?? 0);

  return (
    <ChatCard icon={Tag} title={hasWaterfall ? "Your savings" : "Deals and promotions"}>
      {hasWaterfall && (
        <dl className="space-y-1.5 text-sm">
          <div className="flex items-center justify-between text-muted-foreground">
            <dt>Original total</dt>
            <dd className="tabular-nums">{formatPrice(data.original_total)}</dd>
          </div>
          {savings.map((line, i) => (
            <div key={i} className={`flex items-center justify-between ${TONE_TEXT_CLASS.success}`}>
              <dt>{savingsLabel(line)}</dt>
              <dd className="tabular-nums">−{formatPrice(line.amount)}</dd>
            </div>
          ))}
          <div className="flex items-center justify-between border-t border-border pt-1.5 font-semibold text-foreground">
            <dt>
              Final total
              {data.savings_percentage != null && (
                <span className={`ml-1.5 text-xs font-normal ${TONE_TEXT_CLASS.success}`}>
                  you save {Math.round(data.savings_percentage)}%
                </span>
              )}
            </dt>
            <dd className="tabular-nums">{formatPrice(finalTotal)}</dd>
          </div>
        </dl>
      )}

      {coupons.length > 0 && (
        <CardSection title="Active coupons" icon={Ticket}>
          <DataTable dense columns={couponColumns(coupons, onAction)} rows={coupons} />
        </CardSection>
      )}

      {promotions.length > 0 && (
        <CardSection title="Active promotions" icon={Megaphone}>
          <DataTable dense columns={promotionColumns(promotions)} rows={promotions} />
        </CardSection>
      )}
    </ChatCard>
  );
}
