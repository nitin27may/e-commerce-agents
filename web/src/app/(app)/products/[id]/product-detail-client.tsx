"use client";

import { useState, useCallback } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useLoadEffect } from "@/lib/use-load-effect";
import { useAuth } from "@/lib/auth-context";
import { useCart } from "@/lib/cart-context";
import { api, type ProductDetail } from "@/lib/api";
import { Button, buttonVariants } from "@/components/ui/button";
import { PageContainer } from "@/components/ui/page-header";
import { CHIP_CLASS, TONE_TEXT_CLASS } from "@/components/ui/status-badge";
import { ErrorState } from "@/components/error-state";
import { formatDate, formatPrice } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import {
  Card,
  CardHeader,
  CardTitle,
  CardContent,
} from "@/components/ui/card";
import { Table, TableBody, TableRow, TableCell } from "@/components/ui/table";
import {
  Star,
  ArrowLeft,
  CheckCircle,
  XCircle,
  MessageSquare,
  Loader2,
  MapPin,
  ShoppingCart,
  Check,
  Minus,
  Plus,
} from "lucide-react";
import { productImageUrl } from "@/lib/images";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Spec keys arrive as raw identifiers ("battery_life", "dpi"). */
function specLabel(key: string): string {
  const words = key.replace(/_/g, " ").trim();
  if (words.length <= 3) return words.toUpperCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
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
            className="size-4 fill-warning text-warning"
          />
        ))}
        {hasHalf && (
          <div className="relative">
            <Star className="size-4 text-border" />
            <div className="absolute inset-0 overflow-hidden" style={{ width: "50%" }}>
              <Star className="size-4 fill-warning text-warning" />
            </div>
          </div>
        )}
        {Array.from({ length: emptyStars }).map((_, i) => (
          <Star key={`empty-${i}`} className="size-4 text-border" />
        ))}
      </div>
      <span className="text-sm text-muted-foreground">
        {rating.toFixed(1)} ({count} reviews)
      </span>
    </div>
  );
}

function SmallStarRating({ rating }: { rating: number }) {
  return (
    <div className="flex items-center">
      {Array.from({ length: 5 }).map((_, i) => (
        <Star
          key={i}
          className={`size-3.5 ${
            i < rating
              ? "fill-warning text-warning"
              : "text-border"
          }`}
        />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Rating Distribution Bar Chart
// ---------------------------------------------------------------------------

function RatingDistribution({
  distribution,
}: {
  distribution: Record<string, number>;
}) {
  const entries = [5, 4, 3, 2, 1].map((star) => ({
    star,
    count: distribution[String(star)] || 0,
  }));
  const maxCount = Math.max(...entries.map((e) => e.count), 1);

  return (
    <div className="space-y-2">
      {entries.map(({ star, count }) => (
        <div key={star} className="flex items-center gap-3">
          <span className="w-8 text-right text-sm text-muted-foreground">
            {star}
          </span>
          <Star className="size-3.5 fill-warning text-warning" />
          <div className="flex-1">
            <div className="h-2.5 w-full overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-warning transition-all"
                style={{ width: `${(count / maxCount) * 100}%` }}
              />
            </div>
          </div>
          <span className="w-8 text-sm text-muted-foreground">{count}</span>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Skeleton
// ---------------------------------------------------------------------------

function DetailSkeleton() {
  return (
    <div className="animate-pulse space-y-8">
      <div className="space-y-3">
        <div className="h-4 w-48 rounded bg-muted" />
        <div className="h-8 w-96 rounded bg-muted" />
        <div className="h-4 w-32 rounded bg-muted" />
      </div>
      <div className="grid gap-8 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <div className="h-4 w-full rounded bg-muted" />
          <div className="h-4 w-5/6 rounded bg-muted" />
          <div className="h-4 w-3/4 rounded bg-muted" />
        </div>
        <div className="space-y-3">
          <div className="h-32 rounded-xl bg-muted" />
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function ProductDetailPage() {
  const params = useParams();
  const { user, isLoading: authLoading } = useAuth();

  const { addItem } = useCart();

  const [product, setProduct] = useState<ProductDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [qty, setQty] = useState(1);
  const [added, setAdded] = useState(false);
  const [addingToCart, setAddingToCart] = useState(false);

  const productId = params.id as string;

  const loadProduct = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await api.getProduct(productId);
      setProduct(data);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to load product"
      );
    } finally {
      setLoading(false);
    }
  }, [productId]);

  useLoadEffect(loadProduct, Boolean(user && productId));

  if (authLoading || !user) return null;

  const onSale =
    product?.original_price && product.original_price > product.price;
  const savePct = onSale
    ? Math.round(
        ((product.original_price! - product.price) / product.original_price!) *
          100
      )
    : 0;

  return (
    <PageContainer>
      <Link
        href="/products"
        className={cn(buttonVariants({
          variant: "ghost",
          size: "sm",
          className: "-ml-2 mb-4 text-muted-foreground",
        }))}
      >
        <ArrowLeft data-icon="inline-start" />
        Back to products
      </Link>

      {loading && <DetailSkeleton />}
      {error && (
        <ErrorState inline onRetry={loadProduct} message={error} homeHref="/products" homeLabel="Back to products" />
      )}

      {!loading && !error && product && (
        <>
          {/* Product hero: image + info */}
          <div className="grid gap-8 lg:grid-cols-2">
            <div className="relative aspect-[4/3] overflow-hidden rounded-xl bg-muted ring-1 ring-foreground/10 lg:aspect-square">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={productImageUrl(product.id, 800, 800, product.image_url, product.category, product.name)}
                alt={product.name}
                className="h-full w-full object-cover"
              />
              {onSale && (
                <Badge variant="destructive-solid" className="absolute left-4 top-4 h-6 px-2.5 text-sm">
                  Save {savePct}%
                </Badge>
              )}
            </div>

            <div className="flex flex-col">
              <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
                <Badge variant="outline" className={CHIP_CLASS.slate}>
                  {product.category}
                </Badge>
                {product.brand && <span>{product.brand}</span>}
              </div>
              <h1 className="mt-2 font-heading text-2xl font-semibold tracking-tight text-foreground sm:text-3xl">
                {product.name}
              </h1>
              <div className="mt-2">
                <StarRating rating={product.rating} count={product.review_count} />
              </div>

              <div className="mt-5 flex items-baseline gap-2">
                <span className="text-3xl font-semibold tracking-tight tabular-nums text-foreground">
                  {formatPrice(product.price)}
                </span>
                {onSale && (
                  <span className="text-lg text-muted-foreground line-through tabular-nums">
                    {formatPrice(product.original_price!)}
                  </span>
                )}
              </div>

              <p className="mt-4 leading-relaxed text-muted-foreground">
                {product.description}
              </p>

              <div className="mt-5">
                {product.in_stock ? (
                  <div className={cn("flex items-center gap-2 text-sm font-medium", TONE_TEXT_CLASS.success)}>
                    <CheckCircle className="size-4" aria-hidden />
                    In stock ({product.total_stock} units)
                  </div>
                ) : (
                  <div className="flex items-center gap-2 text-sm font-medium text-destructive">
                    <XCircle className="size-4" aria-hidden />
                    Out of stock
                  </div>
                )}
              </div>

              <div className="mt-6 flex flex-wrap items-center gap-3">
                {product.in_stock && (
                  <>
                    <div className="flex items-center rounded-lg border border-border">
                      <Button
                        variant="ghost"
                        size="icon-lg"
                        aria-label="Decrease quantity"
                        disabled={qty <= 1}
                        onClick={() => setQty((q) => Math.max(1, q - 1))}
                      >
                        <Minus className="size-4" />
                      </Button>
                      <span className="w-8 text-center text-base font-semibold tabular-nums" aria-live="polite">
                        {qty}
                      </span>
                      <Button
                        variant="ghost"
                        size="icon-lg"
                        aria-label="Increase quantity"
                        disabled={qty >= 10}
                        onClick={() => setQty((q) => Math.min(10, q + 1))}
                      >
                        <Plus className="size-4" />
                      </Button>
                    </div>
                    <Button
                      size="xl"
                      className="flex-1 sm:flex-none sm:min-w-48"
                      disabled={addingToCart}
                      onClick={async () => {
                        try {
                          setAddingToCart(true);
                          await addItem(product.id, qty);
                          setAdded(true);
                          setTimeout(() => setAdded(false), 2000);
                        } finally {
                          setAddingToCart(false);
                        }
                      }}
                    >
                      {addingToCart ? (
                        <Loader2 className="animate-spin" data-icon="inline-start" />
                      ) : added ? (
                        <Check data-icon="inline-start" />
                      ) : (
                        <ShoppingCart data-icon="inline-start" />
                      )}
                      {added ? "Added to cart" : "Add to cart"}
                    </Button>
                  </>
                )}
                <Link
                  href={`/chat?q=${encodeURIComponent(`Tell me more about the ${product.name}`)}`}
                  className={cn(buttonVariants({ variant: "outline", size: "xl" }))}
                >
                  <MessageSquare data-icon="inline-start" />
                  Ask about this product
                </Link>
              </div>
            </div>
          </div>

          <div className="mt-10 grid gap-6 lg:grid-cols-3">
            {/* Left column: specs, ratings, reviews */}
            <div className="space-y-6 lg:col-span-2">
              {product.specs && Object.keys(product.specs).length > 0 && (
                <Card>
                  <CardHeader>
                    <CardTitle>Specifications</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <Table>
                      <TableBody>
                        {Object.entries(product.specs).map(([key, value]) => (
                          <TableRow key={key}>
                            <TableCell className="w-1/3 font-medium text-foreground">
                              {specLabel(key)}
                            </TableCell>
                            <TableCell className="text-muted-foreground">
                              {value}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </CardContent>
                </Card>
              )}

              {product.reviews && product.reviews.length > 0 && (
                <Card>
                  <CardHeader>
                    <CardTitle>Reviews ({product.reviews.length})</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    {product.reviews.map((review, idx) => (
                      <div key={review.id || idx}>
                        {idx > 0 && <Separator className="mb-4" />}
                        <div className="space-y-2">
                          <div className="flex items-start justify-between gap-3">
                            <div>
                              <div className="flex items-center gap-2">
                                <span className="text-sm font-medium text-foreground">
                                  {review.reviewer}
                                </span>
                                {review.verified && (
                                  <Badge variant="outline" className={CHIP_CLASS.primary}>
                                    <CheckCircle className="size-2.5" />
                                    Verified
                                  </Badge>
                                )}
                              </div>
                              <SmallStarRating rating={review.rating} />
                            </div>
                            <span className="shrink-0 text-xs text-muted-foreground">
                              {formatDate(review.date)}
                            </span>
                          </div>
                          {review.title && (
                            <p className="text-sm font-medium text-foreground">
                              {review.title}
                            </p>
                          )}
                          <p className="text-sm leading-relaxed text-muted-foreground">
                            {review.body}
                          </p>
                        </div>
                      </div>
                    ))}
                  </CardContent>
                </Card>
              )}
            </div>

            {/* Right column: ratings and stock by warehouse */}
            <div className="space-y-6">
              {product.rating_distribution && (
                <Card>
                  <CardHeader>
                    <CardTitle>Rating breakdown</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <RatingDistribution distribution={product.rating_distribution} />
                  </CardContent>
                </Card>
              )}

              {product.warehouses && product.warehouses.length > 0 && (
                <Card>
                  <CardHeader>
                    <CardTitle>Stock by warehouse</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-2">
                    {product.warehouses.map((ws) => (
                      <div
                        key={ws.name}
                        className="flex items-center justify-between rounded-lg bg-muted px-3 py-2"
                      >
                        <div className="flex items-center gap-2">
                          <MapPin className="size-3.5 text-muted-foreground" aria-hidden />
                          <span className="text-sm text-muted-foreground">{ws.name}</span>
                        </div>
                        <span
                          className={cn(
                            "text-sm font-medium tabular-nums",
                            ws.quantity > 0 ? TONE_TEXT_CLASS.success : "text-muted-foreground",
                          )}
                        >
                          {ws.quantity > 0 ? ws.quantity : "Out of stock"}
                        </span>
                      </div>
                    ))}
                  </CardContent>
                </Card>
              )}
            </div>
          </div>
        </>
      )}
    </PageContainer>
  );
}
