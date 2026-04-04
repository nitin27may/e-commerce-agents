"use client";

import Link from "next/link";
import { ExternalLink } from "lucide-react";
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
}

export function ChatOrderCard({ data }: { data: OrderData }) {
  const orderId = data.id || data.order_id || "";

  return (
    <div className="flex items-center gap-3 rounded-xl border border-slate-200 bg-white p-3 shadow-sm max-w-md">
      <div className="flex flex-1 flex-col gap-1.5 min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-mono text-xs text-slate-500">
            #{orderId.slice(0, 8)}
          </span>
          {data.status && <OrderStatusBadge status={data.status} />}
        </div>
        <div className="flex items-center gap-3 text-xs text-slate-500">
          {data.total != null && (
            <span className="font-medium text-slate-700">
              {formatPrice(data.total)}
            </span>
          )}
          {data.date && <span>{formatDate(data.date)}</span>}
          {data.item_count != null && (
            <span>{data.item_count} items</span>
          )}
        </div>
        {data.tracking && (
          <p className="font-mono text-[10px] text-slate-400">
            Tracking: {data.tracking}
          </p>
        )}
      </div>
      {orderId && (
        <Link href={`/orders/${orderId}`}>
          <Button
            size="sm"
            variant="outline"
            className="h-7 text-xs shrink-0"
          >
            <ExternalLink className="mr-1 size-3" /> View
          </Button>
        </Link>
      )}
    </div>
  );
}
