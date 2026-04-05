"use client";

import Link from "next/link";
import { Star, ExternalLink } from "lucide-react";
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

export function ChatProductCard({ data }: { data: ProductData }) {
  const hasDiscount =
    data.original_price && data.original_price > (data.price || 0);

  return (
    <div className="flex gap-3 rounded-xl border border-slate-200 bg-white p-3 shadow-sm transition-shadow hover:shadow-md max-w-md">
      {data.id && (
        <img
          src={productImageUrl(data.id, 100, 100)}
          alt={data.name}
          className="size-20 shrink-0 rounded-lg object-cover bg-slate-100"
        />
      )}
      <div className="flex flex-1 flex-col gap-1 min-w-0">
        <div className="flex items-start justify-between gap-2">
          <h4 className="text-sm font-semibold text-slate-800 line-clamp-1">
            {data.name}
          </h4>
          {data.category && (
            <Badge variant="outline" className="shrink-0 text-[10px]">
              {data.category}
            </Badge>
          )}
        </div>
        {data.brand && (
          <p className="text-xs text-slate-500">{data.brand}</p>
        )}
        <div className="flex items-center gap-2">
          {data.price != null && (
            <span className="text-sm font-bold text-slate-900">
              ${data.price.toFixed(2)}
            </span>
          )}
          {hasDiscount && (
            <span className="text-xs text-slate-400 line-through">
              ${data.original_price!.toFixed(2)}
            </span>
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
              <Button size="sm" variant="outline" className="h-7 text-xs">
                <ExternalLink className="mr-1 size-3" /> View Details
              </Button>
            </Link>
          </div>
        )}
      </div>
    </div>
  );
}
