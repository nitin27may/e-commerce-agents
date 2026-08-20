"use client";

import Link from "next/link";
import { ShoppingCart, ArrowRight, MapPin } from "lucide-react";
import { Button } from "@/components/ui/button";
import { formatPrice } from "@/lib/format";

interface CheckoutItem {
  name: string;
  quantity: number;
  price?: number;
  unit_price?: number;
  subtotal?: number;
  brand?: string;
}

interface CheckoutData {
  message?: string;
  item_count?: number;
  total?: number;
  subtotal?: number;
  discount?: number;
  items?: CheckoutItem[];
  shipping_address?: string | { street?: string; city?: string; state?: string; zip?: string; country?: string };
  address_ready?: boolean;
}

function formatAddress(addr: CheckoutData["shipping_address"]): string | null {
  if (!addr) return null;
  if (typeof addr === "string") return addr;
  const parts = [addr.street, addr.city, addr.state, addr.zip, addr.country].filter(Boolean);
  return parts.length ? parts.join(", ") : null;
}

export function ChatCheckoutCard({ data }: { data: CheckoutData }) {
  const items = data.items || [];
  const itemCount = data.item_count ?? items.reduce((n, i) => n + (i.quantity || 1), 0);
  const subtotal = data.subtotal ?? items.reduce((s, i) => s + (i.subtotal ?? (i.unit_price ?? i.price ?? 0) * (i.quantity || 1)), 0);
  const total = data.total ?? subtotal - (data.discount ?? 0);
  const addressStr = formatAddress(data.shipping_address);

  return (
    // Checkout's brand accent reuses the chart-2 token (teal-hued already)
    // instead of a literal teal-* — a deliberate accent, not a status color,
    // but still theme-consistent so a future re-skin carries it along.
    <div className="my-2 max-w-md rounded-xl border-2 border-chart-2/30 bg-gradient-to-br from-chart-2/10 to-card overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-3 px-5 pt-4 pb-3">
        <div className="flex size-10 items-center justify-center rounded-full bg-chart-2/15">
          <ShoppingCart className="size-5 text-primary" />
        </div>
        <div className="flex-1 min-w-0">
          <p className="font-semibold text-foreground truncate">{data.message || "Your cart"}</p>
          <p className="text-xs text-muted-foreground">
            {itemCount} {itemCount === 1 ? "item" : "items"}
          </p>
        </div>
      </div>

      {/* Items */}
      {items.length > 0 && (
        <div className="border-y border-chart-2/20 bg-card/60">
          <table className="w-full text-xs">
            <tbody className="divide-y divide-chart-2/10">
              {items.map((item, i) => {
                const unit = item.unit_price ?? item.price ?? 0;
                const lineTotal = item.subtotal ?? unit * (item.quantity || 1);
                return (
                  <tr key={i}>
                    <td className="px-4 py-2">
                      <div className="font-medium text-foreground leading-snug line-clamp-1">
                        {item.name}
                      </div>
                      {item.brand && (
                        <div className="text-[10px] text-muted-foreground mt-0.5">{item.brand}</div>
                      )}
                    </td>
                    <td className="px-2 py-2 text-center text-muted-foreground w-10">
                      {item.quantity}
                    </td>
                    <td className="px-4 py-2 text-right text-foreground font-medium whitespace-nowrap w-20">
                      {formatPrice(lineTotal)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Footer: address + totals + CTA */}
      <div className="px-5 py-3 space-y-2.5">
        {addressStr && (
          <div className="flex items-start gap-2 text-[11px] text-muted-foreground">
            <MapPin className="size-3.5 shrink-0 mt-0.5" />
            <span className="line-clamp-2">{addressStr}</span>
          </div>
        )}
        <div className="flex items-center justify-between">
          <span className="text-xs text-muted-foreground">Total</span>
          <span className="text-lg font-bold text-primary">{formatPrice(total)}</span>
        </div>
        <Link href="/checkout">
          <Button className="w-full gap-2 bg-primary hover:opacity-90 text-primary-foreground">
            Complete Checkout
            <ArrowRight className="size-4" />
          </Button>
        </Link>
      </div>
    </div>
  );
}
