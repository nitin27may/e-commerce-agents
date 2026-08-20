"use client";

import Link from "next/link";
import {
  ExternalLink,
  Package,
  Truck,
  MapPin,
  CalendarDays,
  Clock,
  Navigation,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { OrderStatusBadge } from "@/components/status-badge";
import { formatPrice, formatDate } from "@/lib/format";

interface OrderItem {
  name?: string;
  quantity?: number;
  unit_price?: number;
  total?: number;
  category?: string;
  brand?: string;
}

interface TimelineEvent {
  label?: string;
  status?: string;
  date?: string;
}

interface ShippingAddressObj {
  street?: string;
  city?: string;
  state?: string;
  zip?: string;
  country?: string;
}

interface OrderData {
  id?: string;
  order_id?: string;
  status?: string;
  total?: number;
  date?: string;
  item_count?: number;
  items?: OrderItem[];
  tracking?: string;
  carrier?: string;
  shipping_address?: string | ShippingAddressObj;
  timeline?: TimelineEvent[];
  return_eligible?: boolean;
}

interface ChatOrderCardProps {
  data: OrderData;
  onAction?: (message: string) => void;
}

function formatAddress(addr: OrderData["shipping_address"]): string | null {
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

export function ChatOrderCard({ data, onAction }: ChatOrderCardProps) {
  const orderId = data.id || data.order_id || "";
  const shortId =
    orderId.length > 12
      ? `${orderId.slice(0, 8)}...${orderId.slice(-4)}`
      : orderId;
  const items = data.items || [];
  const timeline = data.timeline || [];
  const returnEligible = isReturnEligible(data);

  return (
    <div className="rounded-xl border border-border bg-card shadow-sm max-w-lg overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between gap-3 border-b border-border bg-muted px-4 py-2.5">
        <div className="flex items-center gap-2">
          <Package className="size-4 text-muted-foreground" />
          <span className="font-mono text-xs font-medium text-muted-foreground">
            #{shortId}
          </span>
          {data.status && <OrderStatusBadge status={data.status} />}
        </div>
        <div className="flex items-center gap-1.5">
          {orderId && (
            <Link href={`/orders/${orderId}`} target="_blank" rel="noopener noreferrer">
              <Button size="sm" variant="outline" className="h-7 text-xs">
                <ExternalLink className="mr-1 size-3" /> View
              </Button>
            </Link>
          )}
          {(data.tracking || data.carrier) && onAction && (
            <Button
              variant="outline"
              size="sm"
              className="h-7 text-xs border-info/30 text-info hover:bg-info/10"
              onClick={() => onAction(`Track the shipment for my order #${data.id || data.order_id}`)}
            >
              <Navigation className="mr-1 size-3" />
              Track
            </Button>
          )}
          {(data.status === "placed" || data.status === "confirmed") && (
            <Button
              variant="outline"
              size="sm"
              className="h-7 text-xs border-destructive/30 text-destructive hover:bg-destructive/10"
              onClick={() => onAction?.(`Cancel my order #${data.id || data.order_id}`)}
            >
              Cancel
            </Button>
          )}
          {returnEligible && (
            <Button
              variant="outline"
              size="sm"
              className="h-7 text-xs border-warning/30 text-warning hover:bg-warning/10"
              onClick={() => onAction?.(`I want to return order #${data.id || data.order_id}`)}
            >
              Return
            </Button>
          )}
        </div>
      </div>

      {/* Items table */}
      {items.length > 0 && (
        <div className="border-b border-border">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-border">
                <th className="px-4 py-1.5 text-left font-medium text-muted-foreground">
                  Item
                </th>
                <th className="px-2 py-1.5 text-center font-medium text-muted-foreground w-12">
                  Qty
                </th>
                <th className="px-4 py-1.5 text-right font-medium text-muted-foreground w-20">
                  Price
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {items.map((item, i) => (
                <tr key={i}>
                  <td className="px-4 py-2">
                    <div className="font-medium text-foreground leading-snug">
                      {item.name}
                    </div>
                    {(item.category || item.brand) && (
                      <div className="text-[10px] text-muted-foreground mt-0.5">
                        {[item.brand, item.category]
                          .filter(Boolean)
                          .join(" \u00b7 ")}
                      </div>
                    )}
                  </td>
                  <td className="px-2 py-2 text-center text-muted-foreground">
                    {item.quantity}
                  </td>
                  <td className="px-4 py-2 text-right text-foreground font-medium whitespace-nowrap">
                    {item.total != null || (item.unit_price != null && item.quantity != null)
                      ? formatPrice(item.total ?? item.unit_price! * item.quantity!)
                      : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Summary footer */}
      <div className="px-4 py-2.5 space-y-1.5">
        {/* Total + date */}
        <div className="flex items-center justify-between">
          {data.total != null && (
            <span className="text-sm font-bold text-foreground">
              Total: {formatPrice(data.total)}
            </span>
          )}
          {data.date && (
            <span className="flex items-center gap-1 text-xs text-muted-foreground">
              <CalendarDays className="size-3" />
              {formatDate(data.date)}
            </span>
          )}
        </div>

        {/* Shipping info */}
        {(data.tracking || data.carrier) && (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Truck className="size-3.5 shrink-0" />
            {data.carrier && <span>{data.carrier}</span>}
            {data.tracking && (
              <>
                {data.carrier && (
                  <span className="text-muted-foreground">&middot;</span>
                )}
                <span className="font-mono text-muted-foreground">
                  {data.tracking}
                </span>
              </>
            )}
          </div>
        )}

        {/* Address */}
        {formatAddress(data.shipping_address) && (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <MapPin className="size-3.5 shrink-0" />
            <span className="line-clamp-1">{formatAddress(data.shipping_address)}</span>
          </div>
        )}

        {/* Timeline (compact) */}
        {timeline.length > 0 && (
          <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground pt-0.5 flex-wrap">
            <Clock className="size-3 shrink-0" />
            {timeline.map((event, i) => (
              <span key={i} className="flex items-center gap-1">
                {i > 0 && <span className="text-muted-foreground">&rarr;</span>}
                <span className="text-muted-foreground">{event.status ?? event.label}</span>
                {event.date && <span>({formatDate(event.date)})</span>}
              </span>
            ))}
          </div>
        )}

        {/* Item count fallback (when no items array) */}
        {items.length === 0 && data.item_count != null && (
          <div className="text-xs text-muted-foreground">
            {data.item_count} item{data.item_count !== 1 ? "s" : ""}
          </div>
        )}
      </div>
    </div>
  );
}
