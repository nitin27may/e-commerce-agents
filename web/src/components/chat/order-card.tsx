"use client";

import Link from "next/link";
import { ExternalLink, Package, Truck, MapPin } from "lucide-react";
import { Button } from "@/components/ui/button";
import { OrderStatusBadge } from "@/components/status-badge";
import { formatPrice, formatDate } from "@/lib/format";

interface OrderData {
  id?: string;
  order_id?: string;
  status?: string;
  total?: number;
  date?: string;
  item_count?: number;
  tracking?: string;
  carrier?: string;
  shipping_address?: string;
}

export function ChatOrderCard({ data }: { data: OrderData }) {
  const orderId = data.id || data.order_id || "";
  const shortId = orderId.length > 12 ? `${orderId.slice(0, 8)}...${orderId.slice(-4)}` : orderId;

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
              <ExternalLink className="mr-1 size-3" /> View Details
            </Button>
          </Link>
        )}
      </div>

      {/* Body */}
      <div className="px-4 py-3 space-y-2">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
          {data.total != null && (
            <span className="font-semibold text-slate-800">
              {formatPrice(data.total)}
            </span>
          )}
          {data.date && (
            <span className="text-slate-500">{formatDate(data.date)}</span>
          )}
          {data.item_count != null && (
            <span className="text-slate-500">
              {data.item_count} item{data.item_count !== 1 ? "s" : ""}
            </span>
          )}
        </div>

        {(data.tracking || data.carrier) && (
          <div className="flex items-center gap-2 text-xs text-slate-500">
            <Truck className="size-3.5 shrink-0" />
            {data.carrier && <span>{data.carrier}</span>}
            {data.tracking && (
              <span className="font-mono text-slate-400">{data.tracking}</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
