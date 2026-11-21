"use client";

import Link from "next/link";
import { Star } from "lucide-react";
import { motion, useReducedMotion } from "framer-motion";
import { formatPrice } from "@/lib/format";
import { productImageUrl } from "@/lib/images";
import { cardHover } from "@/lib/motion";
import { cn } from "@/lib/utils";

export interface ShopProduct {
  id: string;
  name: string;
  price: number;
  original_price?: number | null;
  brand?: string | null;
  category?: string | null;
  image_url?: string | null;
  rating?: number | null;
  review_count?: number | null;
  in_stock?: boolean;
}

/** Storefront product tile — image, name, price, rating; links to detail. */
export function ProductGridCard({ product }: { product: ShopProduct }) {
  const reduce = useReducedMotion();
  const discount =
    product.original_price && product.original_price > product.price
      ? Math.round((1 - product.price / product.original_price) * 100)
      : 0;

  return (
    <motion.div
      variants={reduce ? undefined : cardHover}
      initial="rest"
      whileHover="hover"
    >
      <Link
        href={`/shop/products/${product.id}`}
        className="group/card block overflow-hidden rounded-xl bg-card ring-1 ring-foreground/10 transition-shadow hover:shadow-lg"
      >
        <div className="relative aspect-[4/3] overflow-hidden bg-muted">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={productImageUrl(product.id, 480, 360, product.image_url, product.category)}
            alt={product.name}
            className="size-full object-cover transition-transform duration-300 group-hover/card:scale-105"
          />
          {discount > 0 && (
            <span className="absolute left-2 top-2 rounded-full bg-destructive px-2 py-0.5 text-xs font-semibold text-white">
              -{discount}%
            </span>
          )}
        </div>
        <div className="p-3">
          {product.brand && (
            <p className="text-xs text-muted-foreground">{product.brand}</p>
          )}
          <p className="line-clamp-1 text-sm font-medium">{product.name}</p>
          <div className="mt-1.5 flex items-center justify-between gap-2">
            <div className="flex items-baseline gap-1.5">
              <span className="text-sm font-semibold text-primary">
                {formatPrice(product.price)}
              </span>
              {discount > 0 && (
                <span className="text-xs text-muted-foreground line-through">
                  {formatPrice(product.original_price!)}
                </span>
              )}
            </div>
            {product.rating != null && product.rating > 0 && (
              <span className="flex items-center gap-0.5 text-xs text-muted-foreground">
                <Star className="size-3 fill-amber-400 text-amber-400" />
                {product.rating.toFixed(1)}
              </span>
            )}
          </div>
          {product.in_stock === false && (
            <p className={cn("mt-1 text-xs font-medium text-destructive")}>
              Out of stock
            </p>
          )}
        </div>
      </Link>
    </motion.div>
  );
}
