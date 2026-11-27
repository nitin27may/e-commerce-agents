"use client";

import { Clock, ExternalLink, MapPin, Navigation, Package, RotateCcw, Truck, XCircle } from "lucide-react";
import { OrderStatusBadge } from "@/components/status-badge";
import { formatPrice } from "@/lib/format";
import type { OrderData } from "@/lib/chat-schemas";
import { CardAction, ChatCard } from "./chat-card";
import { formatCardDate, humanizeEnum, shortOrderId } from "./card-format";

interface ChatOrderCardProps {
  data: OrderData;
  onAction?: (message: string) => void;
}

type Address = OrderData["shipping_address"];

export function formatAddress(addr: Address): string | null {
  if (!addr) return null;
  if (typeof addr === "string") return addr;
  const parts = [addr.street, addr.city, addr.state, addr.zip, addr.country].filter(Boolean);
  return parts.length ? parts.join(", ") : null;
}

const RETURN_WINDOW_DAYS = 30;

// The agent may already have called check_return_eligibility and set
// return_eligible explicitly. Otherwise, fall back to a client-side
// heuristic from the delivered timeline date (or the order date as a
// last resort) — same 30-day rule as shared/tools/return_tools.py's
// check_return_eligibility. When there's not enough data to compute a
// date, default to eligible: hiding a legitimately usable Return button
// is worse than occasionally showing one the agent will still refuse.
function isReturnEligible(data: OrderData): boolean {
  if (data.status !== "delivered") return false;
  if (data.return_eligible !== undefined) return data.return_eligible;

  const deliveredEvent = (data.timeline || []).find(
    (e) => (e.status || e.label || "").toLowerCase() === "delivered" && e.date
  );
  const referenceDate = deliveredEvent?.date || data.date;
  if (!referenceDate) return true;

  const parsed = new Date(referenceDate);
  if (Number.isNaN(parsed.getTime())) return true;

  const daysSince = (Date.now() - parsed.getTime()) / (1000 * 60 * 60 * 24);
  return daysSince <= RETURN_WINDOW_DAYS;
}

type OrderItem = NonNullable<OrderData["items"]>[number];

function lineTotal(item: OrderItem): number | null {
  const explicit = typeof item.total === "number" ? item.total : item.subtotal;
  if (explicit != null) return explicit;
  const unit = item.unit_price ?? item.price;
  if (unit != null && item.quantity != null) return unit * item.quantity;
  return null;
}

const NO_TRACKING_STATUSES = new Set(["delivered", "returned", "cancelled"]);

export function ChatOrderCard({ data, onAction }: ChatOrderCardProps) {
  const orderId = data.id || data.order_id || "";
  const items = data.items || [];
  const timeline = data.timeline || [];
  const address = formatAddress(data.shipping_address);
  const canTrack = !!(data.tracking || data.carrier) && !NO_TRACKING_STATUSES.has(data.status ?? "");
  const canCancel = data.status === "placed" || data.status === "confirmed";
  const canReturn = isReturnEligible(data);

  const footer = (orderId || onAction) && (
    <>
      {orderId && (
        <CardAction icon={ExternalLink} href={`/orders/${orderId}`} external>
          View order
        </CardAction>
      )}
      {canTrack && onAction && (
        <CardAction icon={Navigation} onClick={() => onAction(`Track the shipment for my order #${orderId}`)}>
          Track
        </CardAction>
      )}
      {canReturn && (
        <CardAction icon={RotateCcw} onClick={() => onAction?.(`I want to return order #${orderId}`)}>
          Return
        </CardAction>
      )}
      {canCancel && (
        <CardAction icon={XCircle} variant="destructive" onClick={() => onAction?.(`Cancel my order #${orderId}`)}>
          Cancel
        </CardAction>
      )}
    </>
  );

  return (
    <ChatCard
      icon={Package}
      label={orderId ? `Order ${shortOrderId(orderId)}` : "Order"}
      title={
        <span className="flex items-center gap-2">
          <span>Order</span>
          {orderId && (
            <span className="font-mono text-xs text-muted-foreground" title={orderId}>
              #{shortOrderId(orderId)}
            </span>
          )}
          {data.status && <OrderStatusBadge status={data.status} />}
        </span>
      }
      meta={data.date && <span className="text-xs font-normal text-muted-foreground">{formatCardDate(data.date)}</span>}
      footer={footer || undefined}
      flush
    >
      {items.length > 0 && (
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-border">
              <th scope="col" className="px-4 py-1.5 text-left font-medium text-muted-foreground">Item</th>
              <th scope="col" className="w-12 px-2 py-1.5 text-center font-medium text-muted-foreground">Qty</th>
              <th scope="col" className="w-24 px-4 py-1.5 text-right font-medium text-muted-foreground">Price</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {items.map((item, i) => {
              const total = lineTotal(item);
              return (
                <tr key={i}>
                  <td className="px-4 py-2">
                    <div className="font-medium leading-snug text-foreground">{item.name}</div>
                    {(item.category || item.brand) && (
                      <div className="mt-0.5 text-xs text-muted-foreground">
                        {[item.brand, item.category].filter(Boolean).join(" · ")}
                      </div>
                    )}
                  </td>
                  <td className="px-2 py-2 text-center tabular-nums text-muted-foreground">{item.quantity}</td>
                  <td className="whitespace-nowrap px-4 py-2 text-right font-medium tabular-nums text-foreground">
                    {total != null ? formatPrice(total) : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
          {data.total != null && (
            <tfoot>
              <tr className="border-t border-border">
                <th scope="row" colSpan={2} className="px-4 py-2 text-right text-xs font-medium text-muted-foreground">
                  Total
                </th>
                <td className="whitespace-nowrap px-4 py-2 text-right text-sm font-semibold tabular-nums text-foreground">
                  {formatPrice(data.total)}
                </td>
              </tr>
            </tfoot>
          )}
        </table>
      )}

      {(items.length === 0 && (data.total != null || data.item_count != null)) ||
      canTrack ||
      data.tracking ||
      data.carrier ||
      address ||
      timeline.length > 0 ? (
        <div className={`space-y-1.5 px-4 py-3 ${items.length > 0 ? "border-t border-border" : ""}`}>
          {items.length === 0 && (data.total != null || data.item_count != null) && (
            <div className="flex items-center justify-between text-sm">
              <span className="text-muted-foreground">
                {data.item_count != null && `${data.item_count} item${data.item_count !== 1 ? "s" : ""}`}
              </span>
              {data.total != null && (
                <span className="font-semibold tabular-nums text-foreground">Total {formatPrice(data.total)}</span>
              )}
            </div>
          )}

          {(data.tracking || data.carrier) && (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Truck className="size-3.5 shrink-0" aria-hidden />
              {data.carrier && <span>{data.carrier}</span>}
              {data.carrier && data.tracking && <span aria-hidden>·</span>}
              {data.tracking && <span className="font-mono">{data.tracking}</span>}
            </div>
          )}

          {address && (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <MapPin className="size-3.5 shrink-0" aria-hidden />
              <span className="line-clamp-1">{address}</span>
            </div>
          )}

          {timeline.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5 pt-0.5 text-xs text-muted-foreground">
              <Clock className="size-3.5 shrink-0" aria-hidden />
              {timeline.map((event, i) => (
                <span key={i} className="flex items-center gap-1">
                  {i > 0 && <span aria-hidden>→</span>}
                  <span>{humanizeEnum(event.status ?? event.label)}</span>
                  {event.date && <span>({formatCardDate(event.date)})</span>}
                </span>
              ))}
            </div>
          )}
        </div>
      ) : null}
    </ChatCard>
  );
}
