"use client";

import Link from "next/link";
import {
  ExternalLink,
  Package,
  Truck,
  MapPin,
  CalendarDays,
  Clock,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { OrderStatusBadge } from "@/components/status-badge";
import { formatPrice, formatDate } from "@/lib/format";

interface OrderItem {
  name: string;
  quantity: number;
  unit_price: number;
  total?: number;
  category?: string;
  brand?: string;
}

interface TimelineEvent {
  status: string;
  date: string;
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
  shipping_address?: string;
  timeline?: TimelineEvent[];
}

export function ChatOrderCard({ data }: { data: OrderData }) {
  const orderId = data.id || data.order_id || "";
  const shortId =
    orderId.length > 12
      ? `${orderId.slice(0, 8)}...${orderId.slice(-4)}`
      : orderId;
  const items = data.items || [];
  const timeline = data.timeline || [];

  return (
    <div className="rounded-xl border border-slate-200 bg-white shadow-sm max-w-lg overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between gap-3 border-b border-slate-100 bg-slate-50 px-4 py-2.5">
        <div className="flex items-center gap-2">
          <Package className="size-4 text-slate-400" />
          <span className="font-mono text-xs font-medium text-slate-600">
            #{shortId}
          </span>
          {data.status && <OrderStatusBadge status={data.status} />}
        </div>
        {orderId && (
          <Link href={`/orders/${orderId}`}>
            <Button size="sm" variant="outline" className="h-7 text-xs">
              <ExternalLink className="mr-1 size-3" /> View
            </Button>
          </Link>
        )}
      </div>

      {/* Items table */}
      {items.length > 0 && (
        <div className="border-b border-slate-100">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-slate-100">
                <th className="px-4 py-1.5 text-left font-medium text-slate-400">
                  Item
                </th>
                <th className="px-2 py-1.5 text-center font-medium text-slate-400 w-12">
                  Qty
                </th>
                <th className="px-4 py-1.5 text-right font-medium text-slate-400 w-20">
                  Price
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {items.map((item, i) => (
                <tr key={i}>
                  <td className="px-4 py-2">
                    <div className="font-medium text-slate-700 leading-snug">
                      {item.name}
                    </div>
                    {(item.category || item.brand) && (
                      <div className="text-[10px] text-slate-400 mt-0.5">
                        {[item.brand, item.category]
                          .filter(Boolean)
                          .join(" \u00b7 ")}
                      </div>
                    )}
                  </td>
                  <td className="px-2 py-2 text-center text-slate-500">
                    {item.quantity}
                  </td>
                  <td className="px-4 py-2 text-right text-slate-700 font-medium whitespace-nowrap">
                    {formatPrice(
                      item.total ?? item.unit_price * item.quantity
                    )}
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
            <span className="text-sm font-bold text-slate-800">
              Total: {formatPrice(data.total)}
            </span>
          )}
          {data.date && (
            <span className="flex items-center gap-1 text-xs text-slate-400">
              <CalendarDays className="size-3" />
              {formatDate(data.date)}
            </span>
          )}
        </div>

        {/* Shipping info */}
        {(data.tracking || data.carrier) && (
          <div className="flex items-center gap-2 text-xs text-slate-500">
            <Truck className="size-3.5 shrink-0" />
            {data.carrier && <span>{data.carrier}</span>}
            {data.tracking && (
              <>
                {data.carrier && (
                  <span className="text-slate-300">&middot;</span>
                )}
                <span className="font-mono text-slate-400">
                  {data.tracking}
                </span>
              </>
            )}
          </div>
        )}

        {/* Address */}
        {data.shipping_address && (
          <div className="flex items-center gap-2 text-xs text-slate-500">
            <MapPin className="size-3.5 shrink-0" />
            <span className="line-clamp-1">{data.shipping_address}</span>
          </div>
        )}

        {/* Timeline (compact) */}
        {timeline.length > 0 && (
          <div className="flex items-center gap-1.5 text-[10px] text-slate-400 pt-0.5 flex-wrap">
            <Clock className="size-3 shrink-0" />
            {timeline.map((event, i) => (
              <span key={i} className="flex items-center gap-1">
                {i > 0 && <span className="text-slate-300">&rarr;</span>}
                <span className="text-slate-500">{event.status}</span>
                <span>({formatDate(event.date)})</span>
              </span>
            ))}
          </div>
        )}

        {/* Item count fallback (when no items array) */}
        {items.length === 0 && data.item_count != null && (
          <div className="text-xs text-slate-500">
            {data.item_count} item{data.item_count !== 1 ? "s" : ""}
          </div>
        )}
      </div>
    </div>
  );
}
