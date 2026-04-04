"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
  CardFooter,
} from "@/components/ui/card";
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from "@/components/ui/select";
import { Search, Star, Package, Loader2 } from "lucide-react";
import { productImageUrl } from "@/lib/images";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Product {
  id: string;
  name: string;
  brand: string;
  category: string;
  price: number;
  original_price?: number;
  rating: number;
  review_count: number;
  description: string;
  image_url?: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatPrice(price: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
  }).format(price);
}

function StarRating({ rating, count }: { rating: number; count: number }) {
  const fullStars = Math.floor(rating);
  const hasHalf = rating - fullStars >= 0.5;
  const emptyStars = 5 - fullStars - (hasHalf ? 1 : 0);

  return (
    <div className="flex items-center gap-1.5">
      <div className="flex items-center">
        {Array.from({ length: fullStars }).map((_, i) => (
          <Star
            key={`full-${i}`}
            className="size-3.5 fill-amber-400 text-amber-400"
          />
        ))}
        {hasHalf && (
          <div className="relative">
            <Star className="size-3.5 text-slate-200" />
            <div className="absolute inset-0 overflow-hidden" style={{ width: "50%" }}>
              <Star className="size-3.5 fill-amber-400 text-amber-400" />
            </div>
          </div>
        )}
        {Array.from({ length: emptyStars }).map((_, i) => (
          <Star key={`empty-${i}`} className="size-3.5 text-slate-200" />
        ))}
      </div>
      <span className="text-xs text-slate-500">
        {rating.toFixed(1)} ({count} reviews)
      </span>
    </div>
  );
}

const CATEGORY_COLORS: Record<string, string> = {
  electronics: "bg-sky-50 text-sky-700 border-sky-200",
  clothing: "bg-indigo-50 text-indigo-700 border-indigo-200",
  home: "bg-emerald-50 text-emerald-700 border-emerald-200",
  sports: "bg-orange-50 text-orange-700 border-orange-200",
  books: "bg-amber-50 text-amber-700 border-amber-200",
};

function getCategoryColor(category: string): string {
  const key = category.toLowerCase();
  for (const [k, v] of Object.entries(CATEGORY_COLORS)) {
    if (key.includes(k)) return v;
  }
  return "bg-slate-50 text-slate-700 border-slate-200";
}

// ---------------------------------------------------------------------------
// Skeleton loader
// ---------------------------------------------------------------------------

function ProductCardSkeleton() {
  return (
    <Card className="flex flex-col animate-pulse">
      <div className="aspect-[4/3] w-full animate-pulse rounded-t-xl bg-slate-200" />
      <CardHeader>
        <div className="flex items-start justify-between">
          <div className="space-y-2 flex-1">
            <div className="h-4 w-3/4 rounded bg-slate-200" />
            <div className="h-3 w-1/3 rounded bg-slate-200" />
          </div>
          <div className="h-5 w-16 rounded-full bg-slate-200" />
        </div>
      </CardHeader>
      <CardContent className="flex-1 space-y-3">
        <div className="h-3 w-full rounded bg-slate-200" />
        <div className="h-3 w-5/6 rounded bg-slate-200" />
        <div className="h-3 w-1/2 rounded bg-slate-200" />
      </CardContent>
      <CardFooter>
        <div className="h-5 w-20 rounded bg-slate-200" />
      </CardFooter>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Sort options
// ---------------------------------------------------------------------------

const SORT_OPTIONS = [
  { value: "rating", label: "Rating" },
  { value: "price_asc", label: "Price: Low to High" },
  { value: "price_desc", label: "Price: High to Low" },
  { value: "newest", label: "Newest" },
];

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function ProductsPage() {
  const router = useRouter();
  const { user, isLoading: authLoading } = useAuth();

  const [products, setProducts] = useState<Product[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [searchQuery, setSearchQuery] = useState("");
  const [activeCategory, setActiveCategory] = useState<string | null>(null);
  const [sortBy, setSortBy] = useState("rating");

  // Debounced search
  const [debouncedSearch, setDebouncedSearch] = useState("");

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(searchQuery), 300);
    return () => clearTimeout(timer);
  }, [searchQuery]);

  const loadProducts = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await api.getProducts({
        search: debouncedSearch || undefined,
        category: activeCategory || undefined,
        sort: sortBy,
      });
      setProducts(data.products);
      setTotal(data.total);
      setCategories(data.categories);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to load products"
      );
    } finally {
      setLoading(false);
    }
  }, [debouncedSearch, activeCategory, sortBy]);

  useEffect(() => {
    if (user) loadProducts();
  }, [user, loadProducts]);

  if (authLoading || !user) return null;

  return (
    <div className="min-h-screen bg-slate-50">
      {/* Header */}
      <div className="border-b border-slate-200 bg-white">
        <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
          <div className="flex items-center gap-3">
            <div className="flex size-10 items-center justify-center rounded-lg bg-teal-600">
              <Package className="size-5 text-white" />
            </div>
            <div>
              <h1 className="text-2xl font-bold text-slate-900">
                Product Catalog
              </h1>
              <p className="text-sm text-slate-500">
                Browse and discover products across all categories
              </p>
            </div>
          </div>

          {/* Search + Sort row */}
          <div className="mt-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="relative max-w-md flex-1">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
              <Input
                placeholder="Search products..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="pl-9"
              />
            </div>

            <Select value={sortBy} onValueChange={(v) => v && setSortBy(v)}>
              <SelectTrigger className="w-48">
                <SelectValue placeholder="Sort by" />
              </SelectTrigger>
              <SelectContent>
                {SORT_OPTIONS.map((opt) => (
                  <SelectItem key={opt.value} value={opt.value}>
                    {opt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* Category filter pills */}
          {categories.length > 0 && (
            <div className="mt-4 flex flex-wrap gap-2">
              <Button
                variant={activeCategory === null ? "default" : "outline"}
                size="sm"
                className={
                  activeCategory === null
                    ? "bg-teal-600 text-white hover:bg-teal-700"
                    : ""
                }
                onClick={() => setActiveCategory(null)}
              >
                All
              </Button>
              {categories.map((cat) => (
                <Button
                  key={cat}
                  variant={activeCategory === cat ? "default" : "outline"}
                  size="sm"
                  className={
                    activeCategory === cat
                      ? "bg-teal-600 text-white hover:bg-teal-700"
                      : ""
                  }
                  onClick={() => setActiveCategory(cat)}
                >
                  {cat}
                </Button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Content */}
      <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        {/* Result count */}
        {!loading && !error && products.length > 0 && (
          <p className="mb-6 text-sm text-slate-500">
            Showing {products.length} of {total} products
          </p>
        )}

        {/* Loading skeletons */}
        {loading && (
          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <ProductCardSkeleton key={i} />
            ))}
          </div>
        )}

        {/* Error */}
        {error && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        )}

        {/* Empty state */}
        {!loading && !error && products.length === 0 && (
          <div className="py-20 text-center">
            <Package className="mx-auto size-10 text-slate-300" />
            <p className="mt-3 text-sm text-slate-500">
              {debouncedSearch || activeCategory
                ? "No products match your filters."
                : "No products available."}
            </p>
            {(debouncedSearch || activeCategory) && (
              <Button
                variant="outline"
                size="sm"
                className="mt-4"
                onClick={() => {
                  setSearchQuery("");
                  setActiveCategory(null);
                }}
              >
                Clear filters
              </Button>
            )}
          </div>
        )}

        {/* Product grid */}
        {!loading && !error && products.length > 0 && (
          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {products.map((product) => {
              const onSale =
                product.original_price &&
                product.original_price > product.price;

              return (
                <Card
                  key={product.id}
                  className="group flex cursor-pointer flex-col transition-shadow hover:shadow-md"
                  onClick={() => router.push(`/products/${product.id}`)}
                >
                  <div className="relative aspect-[4/3] w-full overflow-hidden rounded-t-xl bg-slate-100">
                    <img
                      src={productImageUrl(product.id)}
                      alt={product.name}
                      className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-105"
                      loading="lazy"
                    />
                    {product.original_price && product.original_price > product.price && (
                      <span className="absolute top-2 left-2 rounded-md bg-red-500 px-2 py-0.5 text-xs font-semibold text-white">
                        {Math.round((1 - product.price / product.original_price) * 100)}% OFF
                      </span>
                    )}
                  </div>
                  <CardHeader>
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0 flex-1">
                        <CardTitle className="truncate text-base">
                          {product.name}
                        </CardTitle>
                        <p className="mt-0.5 text-xs text-slate-500">
                          {product.brand}
                        </p>
                      </div>
                      <Badge
                        variant="outline"
                        className={`shrink-0 text-[10px] font-normal ${getCategoryColor(product.category)}`}
                      >
                        {product.category}
                      </Badge>
                    </div>
                  </CardHeader>

                  <CardContent className="flex-1 space-y-3">
                    <CardDescription className="line-clamp-2 leading-relaxed">
                      {product.description}
                    </CardDescription>

                    <StarRating
                      rating={product.rating}
                      count={product.review_count}
                    />
                  </CardContent>

                  <CardFooter>
                    <div className="flex items-baseline gap-2">
                      <span className="text-lg font-bold text-slate-900">
                        {formatPrice(product.price)}
                      </span>
                      {onSale && (
                        <span className="text-sm text-slate-400 line-through">
                          {formatPrice(product.original_price!)}
                        </span>
                      )}
                    </div>
                  </CardFooter>
                </Card>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
