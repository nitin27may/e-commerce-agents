"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter, useParams } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { useCart } from "@/lib/cart-context";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import {
  Card,
  CardHeader,
  CardTitle,
  CardContent,
} from "@/components/ui/card";
import {
  Table,
  TableHeader,
  TableBody,
  TableHead,
  TableRow,
  TableCell,
} from "@/components/ui/table";
import {
  Star,
  ArrowLeft,
  Package,
  CheckCircle,
  XCircle,
  MessageSquare,
  ChevronRight,
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

interface Review {
  id: string;
  reviewer: string;
  rating: number;
  title: string;
  body: string;
  date: string;
  verified: boolean;
}

interface WarehouseStock {
  name: string;
  region: string;
  quantity: number;
}

interface ProductDetail {
  id: string;
  name: string;
  brand: string;
  category: string;
  price: number;
  original_price?: number;
  image_url?: string;
  rating: number;
  review_count: number;
  description: string;
  specs?: Record<string, string>;
  in_stock: boolean;
  total_stock: number;
  warehouses?: WarehouseStock[];
  rating_distribution?: Record<string, number>;
  reviews?: Review[];
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

function formatDate(dateStr: string): string {
  try {
    return new Date(dateStr).toLocaleDateString("en-US", {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  } catch {
    return dateStr;
  }
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
            className="size-4 fill-amber-400 text-amber-400"
          />
        ))}
        {hasHalf && (
          <div className="relative">
            <Star className="size-4 text-slate-200" />
            <div className="absolute inset-0 overflow-hidden" style={{ width: "50%" }}>
              <Star className="size-4 fill-amber-400 text-amber-400" />
            </div>
          </div>
        )}
        {Array.from({ length: emptyStars }).map((_, i) => (
          <Star key={`empty-${i}`} className="size-4 text-slate-200" />
        ))}
      </div>
      <span className="text-sm text-slate-600">
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
              ? "fill-amber-400 text-amber-400"
              : "text-slate-200"
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
          <span className="w-8 text-right text-sm text-slate-600">
            {star}
          </span>
          <Star className="size-3.5 fill-amber-400 text-amber-400" />
          <div className="flex-1">
            <div className="h-2.5 w-full overflow-hidden rounded-full bg-slate-100">
              <div
                className="h-full rounded-full bg-amber-400 transition-all"
                style={{ width: `${(count / maxCount) * 100}%` }}
              />
            </div>
          </div>
          <span className="w-8 text-sm text-slate-500">{count}</span>
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
        <div className="h-4 w-48 rounded bg-slate-200" />
        <div className="h-8 w-96 rounded bg-slate-200" />
        <div className="h-4 w-32 rounded bg-slate-200" />
      </div>
      <div className="grid gap-8 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <div className="h-4 w-full rounded bg-slate-200" />
          <div className="h-4 w-5/6 rounded bg-slate-200" />
          <div className="h-4 w-3/4 rounded bg-slate-200" />
        </div>
        <div className="space-y-3">
          <div className="h-32 rounded-xl bg-slate-200" />
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function ProductDetailPage() {
  const router = useRouter();
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

  useEffect(() => {
    if (user && productId) loadProduct();
  }, [user, productId, loadProduct]);

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
    <div className="min-h-screen bg-slate-50">
      {/* Header */}
      <div className="border-b border-slate-200 bg-white">
        <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
          {/* Back button */}
          <Button
            variant="ghost"
            size="sm"
            className="mb-4 -ml-2 text-slate-500 hover:text-slate-700"
            onClick={() => router.push("/products")}
          >
            <ArrowLeft className="mr-1.5 size-4" />
            Back to Products
          </Button>

          {loading && <DetailSkeleton />}
          {error && (
            <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              {error}
            </div>
          )}

          {!loading && !error && product && (
            <>
              {/* Breadcrumb */}
              <nav className="mb-4 flex items-center gap-1.5 text-sm text-slate-400">
                <button
                  className="hover:text-teal-600 transition-colors"
                  onClick={() => router.push("/products")}
                >
                  Products
                </button>
                <ChevronRight className="size-3.5" />
                <span>{product.category}</span>
                <ChevronRight className="size-3.5" />
                <span className="text-slate-700">{product.name}</span>
              </nav>

              {/* Product hero: image + info */}
              <div className="grid gap-8 lg:grid-cols-2">
                {/* Image */}
                <div className="relative aspect-square overflow-hidden rounded-2xl bg-slate-100">
                  <img
                    src={productImageUrl(product.id, 800, 800, product.image_url, product.category)}
                    alt={product.name}
                    className="h-full w-full object-cover"
                  />
                  {product.original_price && product.original_price > product.price && (
                    <span className="absolute top-4 left-4 rounded-lg bg-red-500 px-3 py-1 text-sm font-bold text-white shadow-lg">
                      Save {Math.round((1 - product.price / product.original_price) * 100)}%
                    </span>
                  )}
                </div>

                {/* Product Info */}
                <div>
                  <div className="flex items-center gap-3">
                    <h1 className="text-2xl font-bold text-slate-900">
                      {product.name}
                    </h1>
                    <Badge
                      variant="outline"
                      className="bg-slate-50 text-slate-600 border-slate-200"
                    >
                      {product.category}
                    </Badge>
                  </div>
                  <p className="mt-1 text-sm text-slate-500">{product.brand}</p>
                  <div className="mt-3">
                    <StarRating
                      rating={product.rating}
                      count={product.review_count}
                    />
                  </div>

                  <div className="mt-6 flex items-baseline gap-2">
                    <span className="text-3xl font-bold text-slate-900">
                      {formatPrice(product.price)}
                    </span>
                    {onSale && (
                      <span className="text-lg text-slate-400 line-through">
                        {formatPrice(product.original_price!)}
                      </span>
                    )}
                  </div>
                  {onSale && (
                    <Badge className="mt-2 bg-emerald-100 text-emerald-700 border-emerald-200">
                      Save {savePct}%
                    </Badge>
                  )}

                  <p className="mt-6 leading-relaxed text-slate-600">
                    {product.description}
                  </p>

                  <div className="mt-6">
                    {product.in_stock ? (
                      <div className="flex items-center gap-2">
                        <CheckCircle className="size-5 text-emerald-500" />
                        <span className="font-medium text-emerald-700">
                          In Stock ({product.total_stock} units)
                        </span>
                      </div>
                    ) : (
                      <div className="flex items-center gap-2">
                        <XCircle className="size-5 text-red-500" />
                        <span className="font-medium text-red-700">
                          Out of Stock
                        </span>
                      </div>
                    )}
                  </div>

                  {/* Add to Cart */}
                  {product.in_stock && (
                    <div className="mt-6 space-y-3">
                      <div className="flex items-center gap-3">
                        <Button
                          variant="outline"
                          size="icon"
                          className="size-9"
                          disabled={qty <= 1}
                          onClick={() => setQty((q) => Math.max(1, q - 1))}
                        >
                          <Minus className="size-4" />
                        </Button>
                        <span className="w-8 text-center text-lg font-semibold text-slate-800">
                          {qty}
                        </span>
                        <Button
                          variant="outline"
                          size="icon"
                          className="size-9"
                          disabled={qty >= 10}
                          onClick={() => setQty((q) => Math.min(10, q + 1))}
                        >
                          <Plus className="size-4" />
                        </Button>
                      </div>
                      <Button
                        className={`w-full text-base py-5 ${
                          added
                            ? "bg-emerald-600 hover:bg-emerald-700"
                            : "bg-teal-600 hover:bg-teal-700"
                        } text-white`}
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
                          <Loader2 className="mr-2 size-5 animate-spin" />
                        ) : added ? (
                          <Check className="mr-2 size-5" />
                        ) : (
                          <ShoppingCart className="mr-2 size-5" />
                        )}
                        {added ? "Added to Cart!" : "Add to Cart"}
                      </Button>
                    </div>
                  )}
                </div>
              </div>
            </>
          )}
        </div>
      </div>

      {/* Content */}
      {!loading && !error && product && (
        <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
          <div className="grid gap-8 lg:grid-cols-3">
            {/* Left column: description, specs, reviews */}
            <div className="space-y-8 lg:col-span-2">
              {/* Description */}
              <Card>
                <CardHeader>
                  <CardTitle>Description</CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="leading-relaxed text-slate-600">
                    {product.description}
                  </p>
                </CardContent>
              </Card>

              {/* Specs */}
              {product.specs &&
                Object.keys(product.specs).length > 0 && (
                  <Card>
                    <CardHeader>
                      <CardTitle>Specifications</CardTitle>
                    </CardHeader>
                    <CardContent>
                      <Table>
                        <TableBody>
                          {Object.entries(product.specs).map(
                            ([key, value]) => (
                              <TableRow key={key}>
                                <TableCell className="w-1/3 font-medium text-slate-700">
                                  {key}
                                </TableCell>
                                <TableCell className="text-slate-600">
                                  {value}
                                </TableCell>
                              </TableRow>
                            )
                          )}
                        </TableBody>
                      </Table>
                    </CardContent>
                  </Card>
                )}

              {/* Rating Distribution */}
              {product.rating_distribution && (
                <Card>
                  <CardHeader>
                    <CardTitle>Rating Distribution</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <RatingDistribution
                      distribution={product.rating_distribution}
                    />
                  </CardContent>
                </Card>
              )}

              {/* Reviews */}
              {product.reviews && product.reviews.length > 0 && (
                <Card>
                  <CardHeader>
                    <CardTitle>
                      Reviews ({product.reviews.length})
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    {product.reviews.map((review, idx) => (
                      <div key={review.id || idx}>
                        {idx > 0 && <Separator className="mb-4" />}
                        <div className="space-y-2">
                          <div className="flex items-start justify-between">
                            <div>
                              <div className="flex items-center gap-2">
                                <span className="text-sm font-medium text-slate-800">
                                  {review.reviewer}
                                </span>
                                {review.verified && (
                                  <Badge
                                    variant="outline"
                                    className="border-teal-200 bg-teal-50 text-teal-700 text-[10px]"
                                  >
                                    <CheckCircle className="mr-0.5 size-2.5" />
                                    Verified
                                  </Badge>
                                )}
                              </div>
                              <SmallStarRating rating={review.rating} />
                            </div>
                            <span className="text-xs text-slate-400">
                              {formatDate(review.date)}
                            </span>
                          </div>
                          {review.title && (
                            <p className="text-sm font-medium text-slate-800">
                              {review.title}
                            </p>
                          )}
                          <p className="text-sm leading-relaxed text-slate-600">
                            {review.body}
                          </p>
                        </div>
                      </div>
                    ))}
                  </CardContent>
                </Card>
              )}
            </div>

            {/* Right column: stock, ask agent */}
            <div className="space-y-6">
              {/* Stock status */}
              <Card>
                <CardHeader>
                  <CardTitle>Availability</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  {product.in_stock ? (
                    <div className="flex items-center gap-2">
                      <CheckCircle className="size-5 text-emerald-500" />
                      <span className="font-medium text-emerald-700">
                        In Stock ({product.total_stock} units)
                      </span>
                    </div>
                  ) : (
                    <div className="flex items-center gap-2">
                      <XCircle className="size-5 text-red-500" />
                      <span className="font-medium text-red-700">
                        Out of Stock
                      </span>
                    </div>
                  )}

                  {/* Warehouse breakdown */}
                  {product.warehouses &&
                    product.warehouses.length > 0 && (
                      <div className="space-y-2">
                        <p className="text-xs font-medium text-slate-500 uppercase tracking-wider">
                          Warehouse Breakdown
                        </p>
                        {product.warehouses.map((ws) => (
                          <div
                            key={ws.name}
                            className="flex items-center justify-between rounded-lg bg-slate-50 px-3 py-2"
                          >
                            <div className="flex items-center gap-2">
                              <MapPin className="size-3.5 text-slate-400" />
                              <span className="text-sm text-slate-600">
                                {ws.name}
                              </span>
                            </div>
                            <span
                              className={`text-sm font-medium ${
                                ws.quantity > 0
                                  ? "text-emerald-600"
                                  : "text-slate-400"
                              }`}
                            >
                              {ws.quantity}
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                </CardContent>
              </Card>

              {/* Ask Agent button */}
              <Button
                className="w-full bg-teal-600 text-white hover:bg-teal-700"
                onClick={() =>
                  router.push(
                    `/chat?q=${encodeURIComponent(
                      `Tell me more about the ${product.name}`
                    )}`
                  )
                }
              >
                <MessageSquare className="mr-2 size-4" />
                Ask Agent About This Product
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
