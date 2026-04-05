"use client";

import Link from "next/link";
import { Star, ExternalLink, ShoppingBag } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { productImageUrl } from "@/lib/images";

interface ProductData {
  id?: string;
  name?: string;
  price?: number;
  original_price?: number;
  rating?: number;
  category?: string;
  brand?: string;
  description?: string;
}

const CATEGORY_COLORS: Record<string, string> = {
  electronics: "bg-sky-100 text-sky-800 border-sky-200",
  clothing: "bg-violet-100 text-violet-800 border-violet-200",
  home: "bg-emerald-100 text-emerald-800 border-emerald-200",
  sports: "bg-orange-100 text-orange-800 border-orange-200",
  books: "bg-amber-100 text-amber-800 border-amber-200",
};

export function ChatProductCard({ data }: { data: ProductData }) {
  if (!data.name) return null;

  const hasDiscount =
    data.original_price && data.original_price > (data.price || 0);
  const catColor = CATEGORY_COLORS[(data.category || "").toLowerCase()] || "bg-slate-100 text-slate-700 border-slate-200";

  return (
    <div className="flex gap-3 rounded-xl border border-slate-200 bg-white p-3 shadow-sm transition-shadow hover:shadow-md max-w-lg">
      {/* Image or placeholder */}
      <div className="size-16 shrink-0 rounded-lg overflow-hidden bg-slate-100 flex items-center justify-center">
        {data.id ? (
          <img
            src={productImageUrl(data.id, 80, 80)}
            alt={data.name}
            className="size-full object-cover"
            loading="lazy"
          />
        ) : (
          <ShoppingBag className="size-6 text-slate-300" />
        )}
      </div>

      {/* Info */}
      <div className="flex flex-1 flex-col gap-1 min-w-0">
        <div className="flex items-start justify-between gap-2">
          <h4 className="text-sm font-semibold text-slate-800 line-clamp-1">
            {data.name}
          </h4>
          {data.category && (
            <Badge variant="outline" className={`shrink-0 text-[10px] ${catColor}`}>
              {data.category}
            </Badge>
          )}
        </div>

        {data.brand && (
          <p className="text-[11px] text-slate-400">{data.brand}</p>
        )}

        <div className="flex items-center gap-2 mt-0.5">
          {data.price != null && (
            <span className="text-sm font-bold text-teal-700">
              ${data.price.toFixed(2)}
            </span>
          )}
          {hasDiscount && (
            <span className="text-xs text-slate-400 line-through">
              ${data.original_price!.toFixed(2)}
            </span>
          )}
          {hasDiscount && (
            <Badge className="bg-red-500 text-white border-0 text-[9px] px-1.5 py-0">
              {Math.round((1 - (data.price || 0) / data.original_price!) * 100)}% OFF
            </Badge>
          )}
          {data.rating != null && (
            <span className="flex items-center gap-0.5 text-xs text-amber-600">
              <Star className="size-3 fill-amber-400 text-amber-400" />
              {data.rating.toFixed(1)}
            </span>
          )}
        </div>

        {data.id && (
          <div className="mt-1">
            <Link href={`/products/${data.id}`}>
              <Button size="sm" variant="outline" className="h-6 text-[11px] px-2">
                <ExternalLink className="mr-1 size-3" /> View Details
              </Button>
            </Link>
          </div>
        )}
      </div>
    </div>
  );
}
