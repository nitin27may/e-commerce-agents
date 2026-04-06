"use client";

import Link from "next/link";
import { Download, RotateCcw, Package, Clock } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";

interface ReturnData {
  order_id?: string;
  return_id?: string;
  status?: string;
  return_label_url?: string;
  refund_amount?: number;
  refund_method?: string;
  refund_timeline?: string;
}

export function ChatReturnCard({ data }: { data: ReturnData }) {
  return (
    <div className="my-2 max-w-md rounded-xl border border-orange-200 bg-orange-50/50 p-5 space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <RotateCcw className="size-4 text-orange-500" />
          <span className="font-semibold text-slate-900">Return Initiated</span>
        </div>
        {data.status && (
          <Badge variant="outline" className="border-orange-200 bg-orange-50 text-orange-700 text-xs">
            {data.status}
          </Badge>
        )}
      </div>

      {data.return_label_url && (
        <a href={data.return_label_url} target="_blank" rel="noopener noreferrer">
          <Button variant="outline" className="w-full gap-2 border-orange-300 text-orange-700 hover:bg-orange-100">
            <Download className="size-4" />
            Download Return Label
          </Button>
        </a>
      )}

      <p className="text-xs text-slate-500 flex items-center gap-1.5">
        <Package className="size-3.5" />
        Print the label, pack your items, and drop off at any carrier location
      </p>

      <Separator className="bg-orange-200/50" />

      <div className="space-y-1.5 text-sm">
        {data.refund_amount != null && (
          <div className="flex justify-between">
            <span className="text-slate-500">Refund Amount</span>
            <span className="font-medium text-emerald-600">${data.refund_amount.toFixed(2)}</span>
          </div>
        )}
        {data.refund_method && (
          <div className="flex justify-between">
            <span className="text-slate-500">Refund Method</span>
            <span className="text-slate-700">{data.refund_method.replace(/_/g, " ")}</span>
          </div>
        )}
        {data.refund_timeline && (
          <div className="flex justify-between">
            <span className="text-slate-500">Timeline</span>
            <span className="flex items-center gap-1 text-slate-700">
              <Clock className="size-3" />
              {data.refund_timeline}
            </span>
          </div>
        )}
      </div>

      {data.order_id && (
        <Link href={`/orders/${data.order_id}`} className="block text-center text-xs text-teal-600 hover:underline mt-2">
          View Order Details
        </Link>
      )}
    </div>
  );
}
