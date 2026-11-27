"use client";

import { useState, useEffect, useCallback } from "react";
import { useLoadEffect } from "@/lib/use-load-effect";
import { useAuth } from "@/lib/auth-context";
import { useCart } from "@/lib/cart-context";
import { api } from "@/lib/api";
import { asArray, asNumber } from "@/lib/guards";
import { ErrorState } from "@/components/error-state";
import { Button } from "@/components/ui/button";
import { TONE_TEXT_CLASS } from "@/components/ui/status-badge";
import { Input } from "@/components/ui/input";
import { PageContainer, PageHeader } from "@/components/ui/page-header";
import { EmptyState } from "@/components/empty-state";
import { ProductGridCard } from "@/components/shop/product-grid-card";
import { cn } from "@/lib/utils";
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from "@/components/ui/select";
import { Search, Package, ShoppingBag, ShoppingCart, Check } from "lucide-react";

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
  in_stock?: boolean;
}

// ---------------------------------------------------------------------------
// Skeleton loader
// ---------------------------------------------------------------------------

function ProductCardSkeleton() {
  return (
    <div className="overflow-hidden rounded-xl bg-card ring-1 ring-foreground/10">
      <div className="aspect-[4/3] w-full animate-pulse bg-muted" />
      <div className="space-y-2 p-3">
        <div className="h-3 w-1/3 animate-pulse rounded bg-muted" />
        <div className="h-4 w-3/4 animate-pulse rounded bg-muted" />
        <div className="h-4 w-1/4 animate-pulse rounded bg-muted" />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sort options
// ---------------------------------------------------------------------------

const SORT_OPTIONS = [
  { value: "rating", label: "Top rated" },
  { value: "price_asc", label: "Price: low to high" },
  { value: "price_desc", label: "Price: high to low" },
  { value: "newest", label: "Newest" },
];

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function ProductsPage() {
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

  const filtered = Boolean(debouncedSearch || activeCategory);

  return (
    <PageContainer>
      <PageHeader
        icon={ShoppingBag}
        title="Products"
        description="Browse the catalog across every category."
      />

      {/* Search + sort */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
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

        <Select
          items={SORT_OPTIONS}
          value={sortBy}
          onValueChange={(v) => v && setSortBy(v)}
        >
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
            onClick={() => setActiveCategory(null)}
          >
            All
          </Button>
          {categories.map((cat) => (
            <Button
              key={cat}
              variant={activeCategory === cat ? "default" : "outline"}
              size="sm"
              onClick={() => setActiveCategory(cat)}
            >
              {cat}
            </Button>
          ))}
        </div>
      )}

      <div className="mt-6">
        {!loading && !error && products.length > 0 && (
          <p className="mb-4 text-sm text-muted-foreground">
            Showing {products.length} of {total} products
          </p>
        )}

        {loading && (
          <div className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-4">
            {Array.from({ length: 8 }).map((_, i) => (
              <ProductCardSkeleton key={i} />
            ))}
          </div>
        )}

        {error && (
          <ErrorState inline onRetry={loadProducts} message={error} homeHref="/home" homeLabel="Back to home" />
        )}

        {!loading && !error && products.length === 0 && (
          <EmptyState
            icon={Package}
            title={filtered ? "No matching products" : "No products yet"}
            description={
              filtered
                ? "Try a different search or category."
                : "Products will appear here once the catalog is stocked."
            }
          >
            {filtered && (
              <Button
                variant="outline"
                onClick={() => {
                  setSearchQuery("");
                  setActiveCategory(null);
                }}
              >
                Clear filters
              </Button>
            )}
          </EmptyState>
        )}

        {!loading && !error && products.length > 0 && (
          <div className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-4">
            {products.map((product) => {
              const added = addedIds.has(product.id);
              return (
                <div key={product.id} className="relative min-w-0">
                  <ProductGridCard product={product} href={`/products/${product.id}`} />
                  <Button
                    variant="outline"
                    size="icon"
                    aria-label={added ? `${product.name} added to cart` : `Add ${product.name} to cart`}
                    className={cn(
                      "absolute right-2 top-2 bg-card/90 shadow-sm backdrop-blur-sm dark:bg-card/90",
                      added && "border-success/40 bg-success/10 dark:bg-success/15",
                      added && TONE_TEXT_CLASS.success,
                    )}
                    onClick={async () => {
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
                    {added ? <Check className="size-4" /> : <ShoppingCart className="size-4" />}
                  </Button>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </PageContainer>
  );
}
