"use client";

import Link from "next/link";
import { ShoppingCart, ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/button";

interface CheckoutData {
  message?: string;
  item_count?: number;
  total?: number;
}

export function ChatCheckoutCard({ data }: { data: CheckoutData }) {
  const itemCount = data.item_count ?? 0;
  const total = data.total ?? 0;

  return (
    <div className="my-2 max-w-md rounded-xl border-2 border-teal-200 bg-gradient-to-br from-teal-50 to-white p-5">
      <div className="flex items-center gap-3 mb-3">
        <div className="flex size-10 items-center justify-center rounded-full bg-teal-100">
          <ShoppingCart className="size-5 text-teal-600" />
        </div>
        <div>
          <p className="font-semibold text-slate-900">{data.message || "Your cart is ready!"}</p>
          <p className="text-sm text-slate-500">
            {itemCount} {itemCount === 1 ? "item" : "items"} — ${total.toFixed(2)}
          </p>
        </div>
      </div>
      <Link href="/checkout">
        <Button className="w-full gap-2">
          Complete Checkout
          <ArrowRight className="size-4" />
        </Button>
      </Link>
    </div>
  );
}
