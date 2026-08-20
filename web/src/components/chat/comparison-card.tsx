"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Star, ShoppingCart, ExternalLink, Check, AlertCircle, LogIn } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { productImageUrl } from "@/lib/images";
import { useCart } from "@/lib/cart-context";
import { useAuth } from "@/lib/auth-context";

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
  specs?: Record<string, string | number | boolean>;
  on_sale?: boolean;
}

interface ComparisonCardProps {
  products: [ProductData, ProductData];
  onAction?: (message: string) => void;
}

function AddToCartButton({
  product,
  onAction,
}: {
  product: ProductData;
  onAction?: (message: string) => void;
}) {
  const { cart, addItem } = useCart();
  const { isAuthenticated } = useAuth();
  const router = useRouter();
  const [optimistic, setOptimistic] = useState(false);
  const [error, setError] = useState(false);

  const inCart = !!product.id && !!cart?.items?.some((i) => i.product_id === product.id);
  const added = inCart || optimistic;

  if (!product.id) return null;

  if (!isAuthenticated) {
    return (
      <Button
        size="sm"
        variant="outline"
        className="h-7 w-full text-xs"
        onClick={(e) => {
          e.stopPropagation();
          router.push("/login");
        }}
      >
        <LogIn className="mr-1 size-3" />
        Sign in to buy
      </Button>
    );
  }

  return (
    <Button
      size="sm"
      className={`h-7 w-full text-xs ${
        error
          ? "bg-destructive hover:bg-destructive/90 text-white"
          : added
            ? "bg-success hover:bg-success/90 text-success-foreground"
            : "bg-primary hover:opacity-90 text-primary-foreground"
      }`}
      disabled={added && !error}
      onClick={(e) => {
        e.stopPropagation();
        setError(false);
        setOptimistic(true);
        addItem(product.id!)
          .then(() => {
            if (onAction && product.name) {
              onAction(`I just added ${product.name} to my cart. Show me my updated cart.`);
            }
          })
          .catch(() => {
            setOptimistic(false);
            setError(true);
            setTimeout(() => setError(false), 2500);
          });
      }}
    >
      {error ? (
        <AlertCircle className="mr-1 size-3" />
      ) : added ? (
        <Check className="mr-1 size-3" />
      ) : (
        <ShoppingCart className="mr-1 size-3" />
      )}
      {error ? "Retry" : added ? "Added" : "Add to Cart"}
    </Button>
  );
}

function ProductColumn({
  product,
  isBetterValue,
  isBetterRated,
  onAction,
}: {
  product: ProductData;
  isBetterValue: boolean;
  isBetterRated: boolean;
  onAction?: (message: string) => void;
}) {
  const { isAuthenticated } = useAuth();
  return (
    <div className="flex flex-col gap-2 min-w-0">
      {/* Product image */}
      <div className="aspect-square w-full max-w-[120px] mx-auto rounded-lg overflow-hidden bg-muted flex items-center justify-center">
        {product.id ? (
          <img
            src={productImageUrl(product.id, 120, 120, product.image_url, product.category)}
            alt={product.name}
            className="size-full object-cover"
            loading="lazy"
          />
        ) : (
          <div className="size-full bg-muted" />
        )}
      </div>

      {/* Badges */}
      <div className="flex flex-wrap gap-1 justify-center">
        {isBetterValue && (
          <Badge className="bg-success text-success-foreground border-0 text-[9px] px-1.5 py-0">
            Better value
          </Badge>
        )}
        {isBetterRated && (
          <Badge className="bg-warning text-warning-foreground border-0 text-[9px] px-1.5 py-0">
            Higher rated
          </Badge>
        )}
      </div>

      {/* Name */}
      <h4 className="text-xs font-semibold text-foreground text-center line-clamp-2 leading-snug">
        {product.name}
      </h4>

      {/* Brand */}
      {product.brand && (
        <p className="text-[10px] text-muted-foreground text-center">{product.brand}</p>
      )}

      {/* Price */}
      <div className="text-center">
        {product.price != null && (
          <span className="text-base font-bold text-primary">${product.price.toFixed(2)}</span>
        )}
        {product.original_price && product.original_price > (product.price ?? 0) && (
          <span className="ml-1 text-xs text-muted-foreground line-through">
            ${product.original_price.toFixed(2)}
          </span>
        )}
      </div>

      {/* Rating */}
      {product.rating != null && (
        <div className="flex items-center justify-center gap-1">
          <div className="flex">
            {Array.from({ length: 5 }, (_, i) => (
              <Star
                key={i}
                className={`size-2.5 ${
                  i < Math.round(product.rating!)
                    ? "fill-warning text-warning"
                    : "fill-border text-border"
                }`}
              />
            ))}
          </div>
          <span className="text-[10px] text-muted-foreground">
            {product.rating.toFixed(1)}
            {product.review_count != null && ` (${product.review_count})`}
          </span>
        </div>
      )}

      {/* Actions */}
      <div className="flex flex-col gap-1.5 mt-auto pt-1">
        <AddToCartButton product={product} onAction={onAction} />
        {product.id && (
          <Link
            href={isAuthenticated ? `/products/${product.id}` : `/shop/products/${product.id}`}
            target="_blank"
            rel="noopener noreferrer"
            className="block"
          >
            <Button size="sm" variant="outline" className="h-7 w-full text-xs">
              <ExternalLink className="mr-1 size-3" />
              Details
            </Button>
          </Link>
        )}
      </div>
    </div>
  );
}

export function ComparisonCard({ products, onAction }: ComparisonCardProps) {
  const [p1, p2] = products;

  if (!p1.name || !p2.name) return null;

  const p1Price = p1.price ?? Infinity;
  const p2Price = p2.price ?? Infinity;
  const p1Rating = p1.rating ?? 0;
  const p2Rating = p2.rating ?? 0;

  const p1BetterValue = p1Price < p2Price;
  const p2BetterValue = p2Price < p1Price;
  const p1BetterRated = p1Rating > p2Rating;
  const p2BetterRated = p2Rating > p1Rating;

  // Build a shared spec comparison if both have specs
  const sharedSpecKeys = p1.specs && p2.specs
    ? Object.keys(p1.specs).filter((k) => k in p2.specs!)
    : [];

  return (
    <div className="rounded-xl border border-border bg-card shadow-sm overflow-hidden max-w-lg">
      {/* Header */}
      <div className="bg-muted/50 px-4 py-2 border-b border-border">
        <p className="text-xs font-medium text-muted-foreground text-center">Side-by-side comparison</p>
      </div>

      {/* Product columns */}
      <div className="grid grid-cols-2 gap-4 p-4">
        <ProductColumn
          product={p1}
          isBetterValue={p1BetterValue}
          isBetterRated={p1BetterRated}
          onAction={onAction}
        />

        {/* Divider */}
        <div className="absolute left-1/2 top-12 bottom-4 w-px bg-border hidden sm:block" aria-hidden />

        <ProductColumn
          product={p2}
          isBetterValue={p2BetterValue}
          isBetterRated={p2BetterRated}
          onAction={onAction}
        />
      </div>

      {/* Spec comparison table */}
      {sharedSpecKeys.length > 0 && (
        <div className="border-t border-border">
          <table className="w-full text-[11px]">
            <thead>
              <tr className="border-b border-border bg-muted/30">
                <th className="px-3 py-1.5 text-left font-medium text-muted-foreground w-1/3">Spec</th>
                <th className="px-3 py-1.5 text-center font-medium text-foreground w-1/3">{p1.name?.split(" ").slice(0, 2).join(" ")}</th>
                <th className="px-3 py-1.5 text-center font-medium text-foreground w-1/3">{p2.name?.split(" ").slice(0, 2).join(" ")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {sharedSpecKeys.slice(0, 6).map((key) => {
                const v1 = String(p1.specs![key]);
                const v2 = String(p2.specs![key]);
                return (
                  <tr key={key}>
                    <td className="px-3 py-1.5 text-muted-foreground capitalize">{key.replace(/_/g, " ")}</td>
                    <td className={`px-3 py-1.5 text-center ${v1 !== v2 ? "font-medium text-foreground" : "text-muted-foreground"}`}>{v1}</td>
                    <td className={`px-3 py-1.5 text-center ${v1 !== v2 ? "font-medium text-foreground" : "text-muted-foreground"}`}>{v2}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
