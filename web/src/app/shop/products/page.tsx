"use client";

import { Suspense, useCallback, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import { useLoadEffect } from "@/lib/use-load-effect";
import { ProductGridCard, type ShopProduct } from "@/components/shop/product-grid-card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

function Catalog() {
  const params = useSearchParams();
  const search = params.get("search") ?? "";
  const category = params.get("category") ?? "";

  const [products, setProducts] = useState<ShopProduct[] | null>(null);
  const [categories, setCategories] = useState<string[]>([]);

  const loadProducts = useCallback(() => {
    setProducts(null);
    api
      .getProducts({ search: search || undefined, category: category || undefined, sort: "rating" })
      .then((r) => {
        setProducts((r?.products ?? []) as ShopProduct[]);
        setCategories((r?.categories ?? []) as string[]);
      })
      .catch(() => setProducts([]));
  }, [search, category]);
  useLoadEffect(loadProducts, true);

  const heading = search
    ? `Results for “${search}”`
    : category
      ? category
      : "All products";

  return (
    <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold tracking-tight">{heading}</h1>
        <p className="text-sm text-muted-foreground">
          {products == null ? "Loading…" : `${products.length} products`}
        </p>
      </div>

      {/* Category chips */}
      {categories.length > 0 && (
        <div className="mb-6 flex flex-wrap gap-2">
          <Link
            href="/shop/products"
            className={cn(
              "rounded-full border px-3 py-1 text-sm transition-colors hover:bg-accent",
              !category && !search ? "border-primary/40 bg-primary/10 text-primary" : "bg-card",
            )}
          >
            All
          </Link>
          {categories.map((c) => (
            <Link
              key={c}
              href={`/shop/products?category=${encodeURIComponent(c)}`}
              className={cn(
                "rounded-full border px-3 py-1 text-sm transition-colors hover:bg-accent",
                category === c ? "border-primary/40 bg-primary/10 text-primary" : "bg-card",
              )}
            >
              {c}
            </Link>
          ))}
        </div>
      )}

      {products == null ? (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="aspect-[4/3] w-full rounded-xl" />
          ))}
        </div>
      ) : products.length === 0 ? (
        <div className="rounded-xl border border-dashed py-16 text-center text-sm text-muted-foreground">
          No products found. <Link href="/shop/products" className="text-primary hover:underline">Clear filters</Link>
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
          {products.map((p) => (
            <ProductGridCard key={p.id} product={p} />
          ))}
        </div>
      )}
    </div>
  );
}

export default function ProductsPage() {
  // useSearchParams requires a Suspense boundary in Next App Router.
  return (
    <Suspense fallback={<div className="mx-auto max-w-7xl px-4 py-8 sm:px-6" />}>
      <Catalog />
    </Suspense>
  );
}
