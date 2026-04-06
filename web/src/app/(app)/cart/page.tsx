"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useCart } from "@/lib/cart-context";
import { useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";
import { formatPrice } from "@/lib/format";
import { productImageUrl } from "@/lib/images";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import {
  Card,
  CardHeader,
  CardTitle,
  CardContent,
} from "@/components/ui/card";
import {
  ShoppingCart,
  Trash2,
  Plus,
  Minus,
  Tag,
  ArrowRight,
  Loader2,
  ShoppingBag,
  AlertTriangle,
} from "lucide-react";

// ---------------------------------------------------------------------------
// Skeleton loader
// ---------------------------------------------------------------------------

function CartItemSkeleton() {
  return (
    <div className="flex gap-4 animate-pulse py-4">
      <div className="size-20 shrink-0 rounded-lg bg-slate-200" />
      <div className="flex-1 space-y-2">
        <div className="h-4 w-3/4 rounded bg-slate-200" />
        <div className="h-3 w-1/3 rounded bg-slate-200" />
        <div className="h-3 w-1/4 rounded bg-slate-200" />
      </div>
      <div className="h-4 w-16 rounded bg-slate-200" />
    </div>
  );
}

function SummarySkeleton() {
  return (
    <Card className="animate-pulse">
      <CardHeader>
        <div className="h-5 w-32 rounded bg-slate-200" />
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="h-4 w-full rounded bg-slate-200" />
        <div className="h-4 w-full rounded bg-slate-200" />
        <div className="h-px bg-slate-200" />
        <div className="h-6 w-full rounded bg-slate-200" />
        <div className="h-10 w-full rounded bg-slate-200" />
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function CartPage() {
  const router = useRouter();
  const { user, isLoading: authLoading } = useAuth();
  const { cart, itemCount, isLoading, updateItem, removeItem, refreshCart } =
    useCart();

  const [couponInput, setCouponInput] = useState("");
  const [couponLoading, setCouponLoading] = useState(false);
  const [couponError, setCouponError] = useState<string | null>(null);
  const [updatingItems, setUpdatingItems] = useState<Set<string>>(new Set());

  if (authLoading || !user) return null;

  // -- Coupon handlers --

  async function handleApplyCoupon() {
    const code = couponInput.trim();
    if (!code) return;
    setCouponLoading(true);
    setCouponError(null);
    try {
      await api.applyCoupon(code);
      await refreshCart();
      setCouponInput("");
    } catch (err) {
      setCouponError(
        err instanceof Error ? err.message : "Failed to apply coupon"
      );
    } finally {
      setCouponLoading(false);
    }
  }

  async function handleRemoveCoupon() {
    setCouponLoading(true);
    setCouponError(null);
    try {
      await api.removeCoupon();
      await refreshCart();
    } catch (err) {
      setCouponError(
        err instanceof Error ? err.message : "Failed to remove coupon"
      );
    } finally {
      setCouponLoading(false);
    }
  }

  // -- Quantity handlers --

  async function handleUpdateQty(itemId: string, newQty: number) {
    if (newQty < 1) return;
    setUpdatingItems((prev) => new Set(prev).add(itemId));
    try {
      await updateItem(itemId, newQty);
    } finally {
      setUpdatingItems((prev) => {
        const next = new Set(prev);
        next.delete(itemId);
        return next;
      });
    }
  }

  async function handleRemoveItem(itemId: string) {
    setUpdatingItems((prev) => new Set(prev).add(itemId));
    try {
      await removeItem(itemId);
    } finally {
      setUpdatingItems((prev) => {
        const next = new Set(prev);
        next.delete(itemId);
        return next;
      });
    }
  }

  // -- Empty state --

  if (!isLoading && (!cart || cart.items.length === 0)) {
    return (
      <div className="min-h-screen bg-slate-50">
        <div className="border-b border-slate-200 bg-white">
          <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
            <div className="flex items-center gap-3">
              <div className="flex size-10 items-center justify-center rounded-lg bg-teal-600">
                <ShoppingCart className="size-5 text-white" />
              </div>
              <div>
                <h1 className="text-2xl font-bold text-slate-900">
                  Shopping Cart
                </h1>
                <p className="text-sm text-slate-500">0 items</p>
              </div>
            </div>
          </div>
        </div>
        <div className="mx-auto max-w-7xl px-4 py-20 text-center sm:px-6 lg:px-8">
          <ShoppingBag className="mx-auto size-12 text-slate-300" />
          <h2 className="mt-4 text-lg font-semibold text-slate-700">
            Your cart is empty
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            Looks like you haven&apos;t added any products yet.
          </p>
          <Button
            className="mt-6 bg-teal-600 hover:bg-teal-700"
            onClick={() => router.push("/products")}
          >
            Browse Products
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-50">
      {/* Header */}
      <div className="border-b border-slate-200 bg-white">
        <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
          <div className="flex items-center gap-3">
            <div className="flex size-10 items-center justify-center rounded-lg bg-teal-600">
              <ShoppingCart className="size-5 text-white" />
            </div>
            <div>
              <h1 className="text-2xl font-bold text-slate-900">
                Shopping Cart
              </h1>
              <p className="text-sm text-slate-500">
                {isLoading ? "Loading..." : `${itemCount} item${itemCount !== 1 ? "s" : ""}`}
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        {isLoading ? (
          <div className="grid gap-8 lg:grid-cols-3">
            <div className="space-y-0 divide-y divide-slate-200 lg:col-span-2">
              {Array.from({ length: 3 }).map((_, i) => (
                <CartItemSkeleton key={i} />
              ))}
            </div>
            <div>
              <SummarySkeleton />
            </div>
          </div>
        ) : (
          <div className="grid gap-8 lg:grid-cols-3">
            {/* Items column */}
            <div className="lg:col-span-2">
              <Card>
                <CardContent className="divide-y divide-slate-100 p-0">
                  {cart!.items.map((item) => {
                    const isUpdating = updatingItems.has(item.id);
                    const onSale =
                      item.original_price &&
                      item.original_price > item.price;
                    const lowStock =
                      item.in_stock !== false &&
                      item.available_qty != null &&
                      item.available_qty <= 5 &&
                      item.available_qty > 0;
                    const outOfStock = item.in_stock === false;

                    return (
                      <div
                        key={item.id}
                        className={`flex gap-4 p-4 sm:p-6 ${
                          isUpdating ? "opacity-60" : ""
                        }`}
                      >
                        {/* Product image */}
                        <Link
                          href={`/products/${item.product_id}`}
                          className="shrink-0"
                        >
                          <img
                            src={productImageUrl(item.product_id, 80, 80, item.image_url, item.category)}
                            alt={item.name}
                            className="size-20 rounded-lg object-cover bg-slate-100"
                            loading="lazy"
                          />
                        </Link>

                        {/* Item details */}
                        <div className="flex flex-1 flex-col gap-1 min-w-0">
                          <div className="flex items-start justify-between gap-2">
                            <div className="min-w-0">
                              <Link
                                href={`/products/${item.product_id}`}
                                className="text-sm font-medium text-slate-900 hover:text-teal-700 hover:underline line-clamp-1"
                              >
                                {item.name}
                              </Link>
                              <p className="text-xs text-slate-500">
                                {item.brand}
                              </p>
                            </div>
                            <Badge
                              variant="outline"
                              className="shrink-0 text-[10px] font-normal"
                            >
                              {item.category}
                            </Badge>
                          </div>

                          {/* Price */}
                          <div className="flex items-baseline gap-2">
                            <span className="text-sm font-medium text-slate-900">
                              {formatPrice(item.price)}
                            </span>
                            {onSale && (
                              <span className="text-xs text-slate-400 line-through">
                                {formatPrice(item.original_price!)}
                              </span>
                            )}
                          </div>

                          {/* Stock warnings */}
                          {outOfStock && (
                            <div className="flex items-center gap-1 text-xs text-red-600">
                              <AlertTriangle className="size-3" />
                              Out of stock
                            </div>
                          )}
                          {lowStock && (
                            <div className="flex items-center gap-1 text-xs text-amber-600">
                              <AlertTriangle className="size-3" />
                              Only {item.available_qty} left
                            </div>
                          )}

                          {/* Quantity controls + subtotal */}
                          <div className="mt-auto flex items-center justify-between pt-2">
                            <div className="flex items-center gap-1">
                              <Button
                                variant="outline"
                                size="icon"
                                className="size-7"
                                disabled={isUpdating || item.quantity <= 1}
                                onClick={() =>
                                  handleUpdateQty(item.id, item.quantity - 1)
                                }
                              >
                                <Minus className="size-3" />
                              </Button>
                              <span className="w-8 text-center text-sm font-medium text-slate-700">
                                {item.quantity}
                              </span>
                              <Button
                                variant="outline"
                                size="icon"
                                className="size-7"
                                disabled={isUpdating}
                                onClick={() =>
                                  handleUpdateQty(item.id, item.quantity + 1)
                                }
                              >
                                <Plus className="size-3" />
                              </Button>

                              <Button
                                variant="ghost"
                                size="icon"
                                className="ml-2 size-7 text-slate-400 hover:text-red-600"
                                disabled={isUpdating}
                                onClick={() => handleRemoveItem(item.id)}
                              >
                                <Trash2 className="size-3.5" />
                              </Button>
                            </div>

                            <span className="text-sm font-semibold text-slate-900">
                              {formatPrice(item.subtotal)}
                            </span>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </CardContent>
              </Card>
            </div>

            {/* Summary column */}
            <div className="lg:sticky lg:top-8 lg:self-start">
              <Card>
                <CardHeader>
                  <CardTitle>Order Summary</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  {/* Subtotal */}
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-slate-500">Subtotal</span>
                    <span className="text-slate-700">
                      {formatPrice(cart!.subtotal)}
                    </span>
                  </div>

                  {/* Coupon section */}
                  <div className="space-y-2">
                    {cart!.coupon_code ? (
                      <div className="flex items-center justify-between rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2">
                        <div className="flex items-center gap-2">
                          <Tag className="size-3.5 text-emerald-600" />
                          <Badge
                            variant="outline"
                            className="border-emerald-300 bg-emerald-100 text-emerald-700"
                          >
                            {cart!.coupon_code}
                          </Badge>
                        </div>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-auto px-2 py-1 text-xs text-slate-500 hover:text-red-600"
                          disabled={couponLoading}
                          onClick={handleRemoveCoupon}
                        >
                          {couponLoading ? (
                            <Loader2 className="size-3 animate-spin" />
                          ) : (
                            "Remove"
                          )}
                        </Button>
                      </div>
                    ) : (
                      <div className="flex gap-2">
                        <Input
                          placeholder="Coupon code"
                          value={couponInput}
                          onChange={(e) => {
                            setCouponInput(e.target.value);
                            setCouponError(null);
                          }}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") handleApplyCoupon();
                          }}
                          className="h-9 text-sm"
                        />
                        <Button
                          variant="outline"
                          size="sm"
                          className="shrink-0"
                          disabled={couponLoading || !couponInput.trim()}
                          onClick={handleApplyCoupon}
                        >
                          {couponLoading ? (
                            <Loader2 className="size-3 animate-spin" />
                          ) : (
                            "Apply"
                          )}
                        </Button>
                      </div>
                    )}
                    {couponError && (
                      <p className="text-xs text-red-600">{couponError}</p>
                    )}
                  </div>

                  {/* Discount */}
                  {cart!.discount_amount > 0 && (
                    <div className="flex items-center justify-between text-sm">
                      <span className="text-slate-500">Discount</span>
                      <span className="text-emerald-600">
                        -{formatPrice(cart!.discount_amount)}
                      </span>
                    </div>
                  )}

                  <Separator />

                  {/* Total */}
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium text-slate-700">
                      Total
                    </span>
                    <span className="text-xl font-bold text-slate-900">
                      {formatPrice(cart!.total)}
                    </span>
                  </div>

                  {/* Checkout button */}
                  <Button
                    className="w-full bg-teal-600 hover:bg-teal-700"
                    size="lg"
                    onClick={() => router.push("/checkout")}
                  >
                    Proceed to Checkout
                    <ArrowRight className="ml-2 size-4" />
                  </Button>

                  {/* Continue shopping */}
                  <div className="text-center">
                    <Link
                      href="/products"
                      className="text-sm text-slate-500 hover:text-teal-700 hover:underline"
                    >
                      Continue Shopping
                    </Link>
                  </div>
                </CardContent>
              </Card>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
