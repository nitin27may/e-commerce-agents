"use client";

import { CreditCard, MapPin, ShoppingCart } from "lucide-react";
import { TONE_TEXT_CLASS } from "@/components/ui/status-badge";
import { formatPrice } from "@/lib/format";
import type { CheckoutData } from "@/lib/chat-schemas";
import { CardAction, ChatCard } from "./chat-card";
import { formatAddress } from "./order-card";

export function ChatCheckoutCard({ data }: { data: CheckoutData }) {
  const items = data.items || [];
  const itemCount = data.item_count ?? items.reduce((n, i) => n + (i.quantity || 1), 0);
  const subtotal =
    data.subtotal ?? items.reduce((s, i) => s + (i.subtotal ?? (i.unit_price ?? i.price ?? 0) * (i.quantity || 1)), 0);
  const discount = data.discount ?? 0;
  const total = data.total ?? subtotal - discount;
  const address = formatAddress(data.shipping_address);

  return (
    <ChatCard
      icon={ShoppingCart}
      title={data.message || "Your cart"}
      meta={
        <span className="text-xs font-normal text-muted-foreground">
          {itemCount} {itemCount === 1 ? "item" : "items"}
        </span>
      }
      footer={
        <CardAction icon={CreditCard} variant="primary" href="/checkout">
          Complete checkout
        </CardAction>
      }
      flush
    >
      {items.length > 0 && (
        <table className="w-full text-xs">
          <caption className="sr-only">Items in your cart</caption>
          <tbody className="divide-y divide-border">
            {items.map((item, i) => {
              const unit = item.unit_price ?? item.price ?? 0;
              const line = item.subtotal ?? unit * (item.quantity || 1);
              return (
                <tr key={i}>
                  <td className="px-4 py-2">
                    <div className="line-clamp-1 font-medium leading-snug text-foreground">{item.name}</div>
                    {item.brand && <div className="mt-0.5 text-xs text-muted-foreground">{item.brand}</div>}
                  </td>
                  <td className="w-12 px-2 py-2 text-center tabular-nums text-muted-foreground">×{item.quantity ?? 1}</td>
                  <td className="w-24 whitespace-nowrap px-4 py-2 text-right font-medium tabular-nums text-foreground">
                    {formatPrice(line)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      <div className={`space-y-1.5 px-4 py-3 text-sm ${items.length > 0 ? "border-t border-border" : ""}`}>
        {address && (
          <div className="flex items-start gap-2 pb-1 text-xs text-muted-foreground">
            <MapPin className="mt-0.5 size-3.5 shrink-0" aria-hidden />
            <span className="line-clamp-2">{address}</span>
          </div>
        )}
        {discount > 0 && (
          <>
            <div className="flex justify-between text-muted-foreground">
              <span>Subtotal</span>
              <span className="tabular-nums">{formatPrice(subtotal)}</span>
            </div>
            <div className={`flex justify-between ${TONE_TEXT_CLASS.success}`}>
              <span>Discount</span>
              <span className="tabular-nums">−{formatPrice(discount)}</span>
            </div>
          </>
        )}
        <div className="flex items-baseline justify-between">
          <span className="text-muted-foreground">Total</span>
          <span className="text-base font-semibold tabular-nums text-foreground">{formatPrice(total)}</span>
        </div>
      </div>
    </ChatCard>
  );
}
