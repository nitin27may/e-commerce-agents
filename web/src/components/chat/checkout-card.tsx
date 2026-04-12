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
    <div className="my-2 max-w-md rounded-xl border-2 border-teal-200 bg-gradient-to-br from-teal-50 to-white overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-3 px-5 pt-4 pb-3">
        <div className="flex size-10 items-center justify-center rounded-full bg-teal-100">
          <ShoppingCart className="size-5 text-teal-600" />
        </div>
        <div className="flex-1 min-w-0">
          <p className="font-semibold text-slate-900 truncate">{data.message || "Your cart"}</p>
          <p className="text-xs text-slate-500">
            {itemCount} {itemCount === 1 ? "item" : "items"}
          </p>
        </div>
      </div>

      {/* Items */}
      {items.length > 0 && (
        <div className="border-y border-teal-100 bg-white/60">
          <table className="w-full text-xs">
            <tbody className="divide-y divide-teal-50">
              {items.map((item, i) => {
                const unit = item.unit_price ?? item.price ?? 0;
                const lineTotal = item.subtotal ?? unit * (item.quantity || 1);
                return (
                  <tr key={i}>
                    <td className="px-4 py-2">
                      <div className="font-medium text-slate-700 leading-snug line-clamp-1">
                        {item.name}
                      </div>
                      {item.brand && (
                        <div className="text-[10px] text-slate-400 mt-0.5">{item.brand}</div>
                      )}
                    </td>
                    <td className="px-2 py-2 text-center text-slate-500 w-10">
                      {item.quantity}
                    </td>
                    <td className="px-4 py-2 text-right text-slate-700 font-medium whitespace-nowrap w-20">
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
          <div className="flex items-start gap-2 text-[11px] text-slate-500">
            <MapPin className="size-3.5 shrink-0 mt-0.5" />
            <span className="line-clamp-2">{addressStr}</span>
          </div>
        )}
        <div className="flex items-center justify-between">
          <span className="text-xs text-slate-500">Total</span>
          <span className="text-lg font-bold text-teal-700">{formatPrice(total)}</span>
        </div>
        <Link href="/checkout">
          <Button className="w-full gap-2 bg-teal-600 hover:bg-teal-700">
            Complete Checkout
            <ArrowRight className="size-4" />
          </Button>
        </Link>
      </div>
    </div>
  );
}
