"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useCart } from "@/lib/cart-context";
import { useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";
import {
  toastCouponApplied,
  toastCouponFailed,
  toastCartRemoved,
  toastCartUpdated,
} from "@/lib/toast";
import { formatPrice } from "@/lib/format";
import { productImageUrl } from "@/lib/images";
import { Button } from "@/components/ui/button";
import { PageContainer, PageHeader } from "@/components/ui/page-header";
import { CHIP_CLASS, TONE_BADGE_CLASS, TONE_TEXT_CLASS } from "@/components/ui/status-badge";
import { EmptyState } from "@/components/empty-state";
import { cn } from "@/lib/utils";
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
      <div className="size-20 shrink-0 rounded-lg bg-muted" />
      <div className="flex-1 space-y-2">
        <div className="h-4 w-3/4 rounded bg-muted" />
        <div className="h-3 w-1/3 rounded bg-muted" />
        <div className="h-3 w-1/4 rounded bg-muted" />
      </div>
      <div className="h-4 w-16 rounded bg-muted" />
    </div>
  );
}

function SummarySkeleton() {
  return (
    <Card className="animate-pulse">
      <CardHeader>
        <div className="h-5 w-32 rounded bg-muted" />
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="h-4 w-full rounded bg-muted" />
        <div className="h-4 w-full rounded bg-muted" />
        <div className="h-px bg-muted" />
        <div className="h-6 w-full rounded bg-muted" />
        <div className="h-10 w-full rounded bg-muted" />
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
      toastCouponApplied(code);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to apply coupon";
      setCouponError(msg);
      toastCouponFailed(msg);
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
      toastCartUpdated();
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
      toastCartRemoved("Item removed");
    } finally {
      setUpdatingItems((prev) => {
        const next = new Set(prev);
        next.delete(itemId);
        return next;
      });
    }
  }

  // -- Empty state --

  const header = (
    <PageHeader
      icon={ShoppingCart}
      title="Cart"
      description={
        isLoading
          ? "Loading your cart..."
          : `${itemCount} item${itemCount !== 1 ? "s" : ""}`
      }
    />
  );

  if (!isLoading && (!cart || cart.items.length === 0)) {
    return (
      <PageContainer>
        {header}
        <EmptyState
          icon={ShoppingBag}
          title="Your cart is empty"
          description="Products you add to your cart show up here."
        >
          <Button onClick={() => router.push("/products")}>Browse products</Button>
        </EmptyState>
      </PageContainer>
    );
  }

  return (
    <PageContainer>
      {header}
      <div>
        {isLoading ? (
          <div className="grid gap-8 lg:grid-cols-3">
            <div className="space-y-0 divide-y divide-border lg:col-span-2">
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
              <Card className="py-0">
                <CardContent className="divide-y divide-border p-0">
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
                          {/* eslint-disable-next-line @next/next/no-img-element */}
                          <img
                            src={productImageUrl(item.product_id, 160, 160, item.image_url, item.category, item.name)}
                            alt={item.name}
                            className="size-20 rounded-lg object-cover bg-muted"
                            loading="lazy"
                          />
                        </Link>

                        {/* Item details */}
                        <div className="flex flex-1 flex-col gap-1 min-w-0">
                          <div className="flex items-start justify-between gap-2">
                            <div className="min-w-0">
                              <Link
                                href={`/products/${item.product_id}`}
                                className="text-sm font-medium text-foreground hover:text-primary hover:underline line-clamp-1"
                              >
                                {item.name}
                              </Link>
                              <p className="text-xs text-muted-foreground">
                                {item.brand}
                              </p>
                            </div>
                            <Badge
                              variant="outline"
                              className={cn("shrink-0 font-normal", CHIP_CLASS.slate)}
                            >
                              {item.category}
                            </Badge>
                          </div>

                          {/* Price */}
                          <div className="flex items-baseline gap-2">
                            <span className="text-sm font-medium text-foreground">
                              {formatPrice(item.price)}
                            </span>
                            {onSale && (
                              <span className="text-xs text-muted-foreground line-through">
                                {formatPrice(item.original_price!)}
                              </span>
                            )}
                          </div>

                          {/* Stock warnings */}
                          {outOfStock && (
                            <div className="flex items-center gap-1 text-xs text-destructive">
                              <AlertTriangle className="size-3" />
                              Out of stock
                            </div>
                          )}
                          {lowStock && (
                            <div className={cn("flex items-center gap-1 text-xs", TONE_TEXT_CLASS.warning)}>
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
                                aria-label={`Decrease quantity of ${item.name}`}
                                disabled={isUpdating || item.quantity <= 1}
                                onClick={() =>
                                  handleUpdateQty(item.id, item.quantity - 1)
                                }
                              >
                                <Minus className="size-3" />
                              </Button>
                              <span className="w-8 text-center text-sm font-medium tabular-nums text-foreground">
                                {item.quantity}
                              </span>
                              <Button
                                variant="outline"
                                size="icon"
                                className="size-7"
                                aria-label={`Increase quantity of ${item.name}`}
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
                                className="ml-2 size-7 text-muted-foreground hover:text-destructive"
                                aria-label={`Remove ${item.name}`}
                                disabled={isUpdating}
                                onClick={() => handleRemoveItem(item.id)}
                              >
                                <Trash2 className="size-3.5" />
                              </Button>
                            </div>

                            <span className="text-sm font-semibold tabular-nums text-foreground">
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
            <div className="lg:sticky lg:top-6 lg:self-start">
              <Card>
                <CardHeader>
                  <CardTitle>Order summary</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  {/* Subtotal */}
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-muted-foreground">Subtotal</span>
                    <span className="tabular-nums text-foreground">
                      {formatPrice(cart!.subtotal)}
                    </span>
                  </div>

                  {/* Coupon section */}
                  <div className="space-y-2">
                    {cart!.coupon_code ? (
                      <div className="flex items-center justify-between rounded-lg border border-success/30 bg-success/5 px-3 py-2">
                        <div className="flex items-center gap-2">
                          <Tag className={cn("size-3.5", TONE_TEXT_CLASS.success)} aria-hidden />
                          <Badge
                            variant="outline"
                            className={cn("font-mono", TONE_BADGE_CLASS.success)}
                          >
                            {cart!.coupon_code}
                          </Badge>
                        </div>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-auto px-2 py-1 text-xs text-muted-foreground hover:text-destructive"
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
                          aria-label="Coupon code"
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
                      <p className="text-xs text-destructive">{couponError}</p>
                    )}
                  </div>

                  {/* Discount */}
                  {cart!.discount_amount > 0 && (
                    <div className="flex items-center justify-between text-sm">
                      <span className="text-muted-foreground">Discount</span>
                      <span className={cn("tabular-nums", TONE_TEXT_CLASS.success)}>
                        -{formatPrice(cart!.discount_amount)}
                      </span>
                    </div>
                  )}

                  <Separator />

                  {/* Total */}
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium text-muted-foreground">
                      Total
                    </span>
                    <span className="text-xl font-semibold tabular-nums text-foreground">
                      {formatPrice(cart!.total)}
                    </span>
                  </div>

                  {/* Checkout button */}
                  <Button
                    className="w-full"
                    size="xl"
                    onClick={() => router.push("/checkout")}
                  >
                    Proceed to checkout
                    <ArrowRight data-icon="inline-end" />
                  </Button>

                  {/* Continue shopping */}
                  <div className="text-center">
                    <Link
                      href="/products"
                      className="text-sm text-muted-foreground hover:text-primary hover:underline"
                    >
                      Continue shopping
                    </Link>
                  </div>
                </CardContent>
              </Card>
            </div>
          </div>
        )}
      </div>
    </PageContainer>
  );
}
