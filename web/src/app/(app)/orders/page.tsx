"use client";

import { useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useLoadEffect } from "@/lib/use-load-effect";
import { useAuth } from "@/lib/auth-context";
import { api, type OrderSummary as Order } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { CHIP_CLASS, TONE_BADGE_CLASS } from "@/components/ui/status-badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardHeader,
  CardTitle,
  CardContent,
} from "@/components/ui/card";
import {
  ShoppingCart,
  Package,
  Truck,
  ChevronRight,
  Loader2,
  Clock,
  CheckCircle,
  XCircle,
  RotateCcw,
} from "lucide-react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

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

const STATUS_CONFIG: Record<
  string,
  { color: string; icon: React.ElementType; label: string }
> = {
  placed: {
    color: TONE_BADGE_CLASS.info,
    icon: Clock,
    label: "Placed",
  },
  confirmed: {
    color: CHIP_CLASS.primary,
    icon: CheckCircle,
    label: "Confirmed",
  },
  shipped: {
    color: TONE_BADGE_CLASS.warning,
    icon: Truck,
    label: "Shipped",
  },
  delivered: {
    color: TONE_BADGE_CLASS.success,
    icon: CheckCircle,
    label: "Delivered",
  },
  returned: {
    color: TONE_BADGE_CLASS.neutral,
    icon: RotateCcw,
    label: "Returned",
  },
  cancelled: {
    color: TONE_BADGE_CLASS.destructive,
    icon: XCircle,
    label: "Cancelled",
  },
};

function getStatusConfig(status: string) {
  const key = status.toLowerCase();
  return (
    STATUS_CONFIG[key] || {
      color: "border-border bg-muted/50 text-muted-foreground",
      icon: Package,
      label: status,
    }
  );
}

// ---------------------------------------------------------------------------
// Status tabs
// ---------------------------------------------------------------------------

const STATUS_TABS = [
  { value: "", label: "All" },
  { value: "placed", label: "Placed" },
  { value: "confirmed", label: "Confirmed" },
  { value: "shipped", label: "Shipped" },
  { value: "delivered", label: "Delivered" },
  { value: "returned", label: "Returned" },
  { value: "cancelled", label: "Cancelled" },
];

// ---------------------------------------------------------------------------
// Skeleton
// ---------------------------------------------------------------------------

function OrderCardSkeleton() {
  return (
    <Card className="animate-pulse">
      <CardContent className="space-y-3 py-4">
        <div className="flex items-center justify-between">
          <div className="h-4 w-32 rounded bg-muted" />
          <div className="h-5 w-20 rounded-full bg-muted" />
        </div>
        <div className="h-3 w-48 rounded bg-muted" />
        <div className="flex items-center justify-between">
          <div className="h-4 w-24 rounded bg-muted" />
          <div className="h-4 w-16 rounded bg-muted" />
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function OrdersPage() {
  const router = useRouter();
  const { user, isLoading: authLoading } = useAuth();

  const [orders, setOrders] = useState<Order[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeStatus, setActiveStatus] = useState("");

  const loadOrders = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await api.getOrders(activeStatus || undefined);
      setOrders(data.orders);
      setTotal(data.total);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to load orders"
      );
    } finally {
      setLoading(false);
    }
  }, [activeStatus]);

  useLoadEffect(loadOrders, Boolean(user));

  if (authLoading || !user) return null;

  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      <div className="border-b border-border bg-card">
        <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
          <div className="flex items-center gap-3">
            <div className="flex size-10 items-center justify-center rounded-lg bg-primary">
              <ShoppingCart className="size-5 text-primary-foreground" />
            </div>
            <div>
              <h1 className="text-2xl font-bold text-foreground">
                Order History
              </h1>
              <p className="text-sm text-muted-foreground">
                View and track all your orders
              </p>
            </div>
          </div>

          {/* Status filter tabs */}
          <div className="mt-6 flex flex-wrap gap-2">
            {STATUS_TABS.map((tab) => (
              <Button
                key={tab.value}
                variant={activeStatus === tab.value ? "default" : "outline"}
                size="sm"
                className={
                  activeStatus === tab.value
                    ? "bg-primary text-primary-foreground hover:opacity-90"
                    : ""
                }
                onClick={() => setActiveStatus(tab.value)}
              >
                {tab.label}
              </Button>
            ))}
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        {/* Total count */}
        {!loading && !error && orders.length > 0 && (
          <p className="mb-6 text-sm text-muted-foreground">{total} orders</p>
        )}

        {/* Loading */}
        {loading && (
          <div className="space-y-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <OrderCardSkeleton key={i} />
            ))}
          </div>
        )}

        {/* Error */}
        {error && (
          <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        )}

        {/* Empty state */}
        {!loading && !error && orders.length === 0 && (
          <div className="py-20 text-center">
            <ShoppingCart className="mx-auto size-10 text-muted-foreground" />
            <p className="mt-3 text-sm text-muted-foreground">
              {activeStatus
                ? `No ${activeStatus} orders found.`
                : "No orders yet."}
            </p>
            {activeStatus && (
              <Button
                variant="outline"
                size="sm"
                className="mt-4"
                onClick={() => setActiveStatus("")}
              >
                View all orders
              </Button>
            )}
          </div>
        )}

        {/* Order list */}
        {!loading && !error && orders.length > 0 && (
          <div className="space-y-4">
            {orders.map((order) => {
              const statusCfg = getStatusConfig(order.status);
              const StatusIcon = statusCfg.icon;

              return (
                <Card
                  key={order.id}
                  className="cursor-pointer transition-shadow hover:shadow-md"
                  onClick={() => router.push(`/orders/${order.id}`)}
                >
                  <CardContent className="py-4">
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                      {/* Left section */}
                      <div className="flex-1 space-y-2">
                        <div className="flex flex-wrap items-center gap-3">
                          <span className="font-mono text-sm font-medium text-foreground">
                            #{order.id}
                          </span>
                          <Badge
                            variant="outline"
                            className={statusCfg.color}
                          >
                            <StatusIcon className="mr-1 size-3" />
                            {statusCfg.label}
                          </Badge>
                        </div>

                        <div className="flex flex-wrap items-center gap-4 text-sm text-muted-foreground">
                          <span>{formatDate(order.date)}</span>
                          <span>
                            {order.item_count} item
                            {order.item_count !== 1 ? "s" : ""}
                          </span>
                          {order.carrier && (
                            <span className="flex items-center gap-1">
                              <Truck className="size-3.5" />
                              {order.carrier}
                              {order.tracking && (
                                <span className="font-mono text-xs">
                                  {order.tracking}
                                </span>
                              )}
                            </span>
                          )}
                        </div>
                      </div>

                      {/* Right section */}
                      <div className="flex items-center gap-3">
                        <span className="text-lg font-bold text-foreground">
                          {formatPrice(order.total)}
                        </span>
                        <ChevronRight className="size-5 text-muted-foreground" />
                      </div>
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
