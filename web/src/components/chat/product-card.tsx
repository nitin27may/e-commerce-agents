"use client";

import { useState } from "react";
import Link from "next/link";
import { Star, ExternalLink, ShoppingCart, ShoppingBag, Check, Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { productImageUrl } from "@/lib/images";
import { useCart } from "@/lib/cart-context";

interface ProductData {
  id?: string;
  name?: string;
  price?: number;
  original_price?: number;
  image_url?: string;
  rating?: number;
  review_count?: number;
  category?: string;
  brand?: string;
  description?: string;
  on_sale?: boolean;
}

const CATEGORY_COLORS: Record<string, string> = {
  electronics: "bg-sky-100 text-sky-800 border-sky-200",
  clothing: "bg-violet-100 text-violet-800 border-violet-200",
  home: "bg-emerald-100 text-emerald-800 border-emerald-200",
  sports: "bg-orange-100 text-orange-800 border-orange-200",
  books: "bg-amber-100 text-amber-800 border-amber-200",
};

interface ChatProductCardProps {
  data: ProductData;
  onAction?: (message: string) => void;
}

export function ChatProductCard({ data, onAction }: ChatProductCardProps) {
  const [added, setAdded] = useState(false);
  const [adding, setAdding] = useState(false);
  const { addItem } = useCart();

  if (!data.name) return null;

  const hasDiscount =
    data.original_price && data.original_price > (data.price || 0);
  const discountPct = hasDiscount
    ? Math.round((1 - (data.price || 0) / data.original_price!) * 100)
    : 0;
  const catColor =
    CATEGORY_COLORS[(data.category || "").toLowerCase()] ||
    "bg-slate-100 text-slate-700 border-slate-200";

  return (
    <div className="rounded-xl border border-slate-200 bg-white shadow-sm max-w-md overflow-hidden transition-shadow hover:shadow-md">
      {/* Top: image + info */}
      <div className="flex gap-3 p-3 pb-2">
        {/* Image */}
        <div className="size-20 shrink-0 rounded-lg overflow-hidden bg-slate-100 flex items-center justify-center">
          {data.id ? (
            <img
              src={productImageUrl(data.id, 100, 100, data.image_url, data.category)}
              alt={data.name}
              className="size-full object-cover"
              loading="lazy"
            />
          ) : (
            <ShoppingBag className="size-7 text-slate-300" />
          )}
        </div>

        {/* Info */}
        <div className="flex flex-1 flex-col gap-0.5 min-w-0">
          <h4 className="text-sm font-semibold text-slate-800 line-clamp-2 leading-tight">
            {data.name}
          </h4>

          <div className="flex items-center gap-1.5 flex-wrap">
            {data.brand && (
              <span className="text-[11px] text-slate-400">{data.brand}</span>
            )}
            {data.category && (
              <Badge
                variant="outline"
                className={`text-[10px] px-1.5 py-0 ${catColor}`}
              >
                {data.category}
              </Badge>
            )}
          </div>

          {data.description && (
            <p className="text-[11px] text-slate-500 line-clamp-2 leading-snug">
              {data.description}
            </p>
          )}

          {/* Price */}
          <div className="flex items-center gap-1.5 mt-auto pt-0.5">
            {data.price != null && (
              <span className="text-base font-bold text-teal-700">
                ${data.price.toFixed(2)}
              </span>
            )}
            {hasDiscount && (
              <span className="text-xs text-slate-400 line-through">
                ${data.original_price!.toFixed(2)}
              </span>
            )}
            {hasDiscount && discountPct > 0 && (
              <Badge className="bg-red-500 text-white border-0 text-[9px] px-1.5 py-0">
                {discountPct}% OFF
              </Badge>
            )}
          </div>

          {/* Rating */}
          {data.rating != null && (
            <div className="flex items-center gap-1">
              <div className="flex items-center">
                {Array.from({ length: 5 }, (_, i) => (
                  <Star
                    key={i}
                    className={`size-3 ${
                      i < Math.round(data.rating!)
                        ? "fill-amber-400 text-amber-400"
                        : "fill-slate-200 text-slate-200"
                    }`}
                  />
                ))}
              </div>
              <span className="text-[11px] text-slate-500">
                {data.rating.toFixed(1)}
                {data.review_count != null && ` (${data.review_count})`}
              </span>
            </div>
          )}
        </div>
      </div>

      {/* Action buttons */}
      {(data.id || onAction) && (
        <div className="flex items-center gap-2 border-t border-slate-100 px-3 py-2">
          {data.id && (
            <Button
              size="sm"
              className={`h-7 text-xs ${
                added
                  ? "bg-emerald-600 hover:bg-emerald-700 text-white"
                  : "bg-teal-600 hover:bg-teal-700 text-white"
              }`}
              disabled={adding}
              onClick={async (e) => {
                e.stopPropagation();
                if (!data.id || adding) return;
                setAdding(true);
                try {
                  await addItem(data.id);
                  setAdded(true);
                  setTimeout(() => setAdded(false), 2000);
                } catch { /* ignore */ }
                setAdding(false);
              }}
            >
              {adding ? (
                <Loader2 className="mr-1 size-3 animate-spin" />
              ) : added ? (
                <Check className="mr-1 size-3" />
              ) : (
                <ShoppingCart className="mr-1 size-3" />
              )}
              {added ? "Added!" : "Add to Cart"}
            </Button>
          )}
          {data.id && (
            <Link href={`/products/${data.id}`}>
              <Button size="sm" variant="outline" className="h-7 text-xs">
                <ExternalLink className="mr-1 size-3" />
                Details
              </Button>
            </Link>
          )}
        </div>
      )}
    </div>
  );
}
