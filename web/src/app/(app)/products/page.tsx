"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useLoadEffect } from "@/lib/use-load-effect";
import { useAuth } from "@/lib/auth-context";
import { useCart } from "@/lib/cart-context";
import { api } from "@/lib/api";
import { asArray, asNumber } from "@/lib/guards";
import { ErrorState } from "@/components/error-state";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { CHIP_CLASS, TONE_BADGE_CLASS } from "@/components/ui/status-badge";
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
import { Search, Star, Package, Loader2, ShoppingCart, Check } from "lucide-react";
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

function StarRating({ rating: rawRating, count: rawCount }: { rating?: number; count?: number }) {
  // A product with no reviews yet may arrive without either field.
  const rating = asNumber(rawRating);
  const count = asNumber(rawCount);
  const fullStars = Math.floor(rating);
  const hasHalf = rating - fullStars >= 0.5;
  const emptyStars = 5 - fullStars - (hasHalf ? 1 : 0);

  return (
    <div className="flex items-center gap-1.5">
      <div className="flex items-center">
        {Array.from({ length: fullStars }).map((_, i) => (
          <Star
            key={`full-${i}`}
            className="size-3.5 fill-warning text-warning"
          />
        ))}
        {hasHalf && (
          <div className="relative">
            <Star className="size-3.5 text-border" />
            <div className="absolute inset-0 overflow-hidden" style={{ width: "50%" }}>
              <Star className="size-3.5 fill-warning text-warning" />
            </div>
          </div>
        )}
        {Array.from({ length: emptyStars }).map((_, i) => (
          <Star key={`empty-${i}`} className="size-3.5 text-border" />
        ))}
      </div>
      <span className="text-xs text-muted-foreground">
        {rating.toFixed(1)} ({count} reviews)
      </span>
    </div>
  );
}

const CATEGORY_COLORS: Record<string, string> = {
  electronics: CHIP_CLASS.primary,
  clothing: CHIP_CLASS.teal,
  home: TONE_BADGE_CLASS.success,
  sports: TONE_BADGE_CLASS.warning,
  books: CHIP_CLASS.slate,
};

function getCategoryColor(category: string): string {
  const key = category.toLowerCase();
  for (const [k, v] of Object.entries(CATEGORY_COLORS)) {
    if (key.includes(k)) return v;
  }
  return "bg-muted text-muted-foreground border-border";
}

// ---------------------------------------------------------------------------
// Skeleton loader
// ---------------------------------------------------------------------------

function ProductCardSkeleton() {
  return (
    <Card className="flex flex-col animate-pulse">
      <div className="aspect-[4/3] w-full animate-pulse rounded-t-xl bg-muted" />
      <CardHeader>
        <div className="flex items-start justify-between">
          <div className="space-y-2 flex-1">
            <div className="h-4 w-3/4 rounded bg-muted" />
            <div className="h-3 w-1/3 rounded bg-muted" />
          </div>
          <div className="h-5 w-16 rounded-full bg-muted" />
        </div>
      </CardHeader>
      <CardContent className="flex-1 space-y-3">
        <div className="h-3 w-full rounded bg-muted" />
        <div className="h-3 w-5/6 rounded bg-muted" />
        <div className="h-3 w-1/2 rounded bg-muted" />
      </CardContent>
      <CardFooter>
        <div className="h-5 w-20 rounded bg-muted" />
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
  const { addItem } = useCart();

  const [products, setProducts] = useState<Product[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [addedIds, setAddedIds] = useState<Set<string>>(new Set());

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
      const list = asArray<Product>(data?.products);
      setProducts(list);
      setTotal(asNumber(data?.total, list.length));
      setCategories(asArray<string>(data?.categories));
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to load products"
      );
    } finally {
      setLoading(false);
    }
  }, [debouncedSearch, activeCategory, sortBy]);

  useLoadEffect(loadProducts, Boolean(user));

  if (authLoading || !user) return null;

  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      <div className="border-b border-border bg-card">
        <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
          <div className="flex items-center gap-3">
            <div className="flex size-10 items-center justify-center rounded-lg bg-primary">
              <Package className="size-5 text-primary-foreground" />
            </div>
            <div>
              <h1 className="text-2xl font-bold text-foreground">
                Product Catalog
              </h1>
              <p className="text-sm text-muted-foreground">
                Browse and discover products across all categories
              </p>
            </div>
          </div>

          {/* Search + Sort row */}
          <div className="mt-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="relative max-w-md flex-1">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                placeholder="Search products..."
                aria-label="Search products"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="pl-9"
              />
            </div>

            <Select value={sortBy} onValueChange={(v) => v && setSortBy(v)}>
              <SelectTrigger className="w-48" aria-label="Sort products">
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
                    ? "bg-primary text-primary-foreground hover:opacity-90"
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
                      ? "bg-primary text-primary-foreground hover:opacity-90"
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
          <p className="mb-6 text-sm text-muted-foreground">
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
          <ErrorState inline onRetry={loadProducts} message={error} homeHref="/home" homeLabel="Back to dashboard" />
        )}

        {/* Empty state */}
        {!loading && !error && products.length === 0 && (
          <div className="py-20 text-center">
            <Package className="mx-auto size-10 text-muted-foreground" />
            <p className="mt-3 text-sm text-muted-foreground">
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
                  <div className="relative aspect-[4/3] w-full overflow-hidden rounded-t-xl bg-muted">
                    <img
                      src={productImageUrl(product.id, 400, 300, product.image_url, product.category)}
                      alt={product.name}
                      className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-105"
                      loading="lazy"
                    />
                    {product.original_price && product.original_price > product.price && (
                      <span className="absolute top-2 left-2 rounded-md bg-destructive px-2 py-0.5 text-xs font-semibold text-white">
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
                        <p className="mt-0.5 text-xs text-muted-foreground">
                          {product.brand}
                        </p>
                      </div>
                      <Badge
                        variant="outline"
                        className={`shrink-0 text-xs font-normal ${getCategoryColor(product.category)}`}
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

                  <CardFooter className="flex items-center justify-between">
                    <div className="flex items-baseline gap-2">
                      <span className="text-lg font-bold text-foreground">
                        {formatPrice(product.price)}
                      </span>
                      {onSale && (
                        <span className="text-sm text-muted-foreground line-through">
                          {formatPrice(product.original_price!)}
                        </span>
                      )}
                    </div>
                    <Button
                      variant="outline"
                      size="icon"
                      aria-label={`Add ${product.name} to cart`}
                      className={`shrink-0 transition-colors ${
                        addedIds.has(product.id)
                          ? "border-success/40 bg-success/10 text-[color-mix(in_oklab,var(--success)_62%,black)] dark:text-success"
                          : "hover:border-primary/40 hover:bg-primary/10 hover:text-primary"
                      }`}
                      onClick={async (e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        await addItem(product.id);
                        setAddedIds((prev) => new Set(prev).add(product.id));
                        setTimeout(() => {
                          setAddedIds((prev) => {
                            const next = new Set(prev);
                            next.delete(product.id);
                            return next;
                          });
                        }, 2000);
                      }}
                    >
                      {addedIds.has(product.id) ? (
                        <Check className="size-4" />
                      ) : (
                        <ShoppingCart className="size-4" />
                      )}
                    </Button>
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
