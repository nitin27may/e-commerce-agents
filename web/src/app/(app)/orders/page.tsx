"use client";

import { useState, useCallback } from "react";
import Link from "next/link";
import { useLoadEffect } from "@/lib/use-load-effect";
import { useAuth } from "@/lib/auth-context";
import { api, type OrderSummary as Order } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { PageContainer, PageHeader } from "@/components/ui/page-header";
import { OrderStatusBadge, STATUS_CONFIG } from "@/components/status-badge";
import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { formatDate, formatPrice } from "@/lib/format";
import { Package, Truck, ChevronRight } from "lucide-react";

function getStatusLabel(status: string): string {
  return STATUS_CONFIG[status]?.label ?? status;
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
    <PageContainer>
      <PageHeader icon={Package} title="Orders" description="Track and manage everything you have ordered." />

      {/* Status filter */}
      <div className="flex flex-wrap gap-2" role="group" aria-label="Filter by status">
        {STATUS_TABS.map((tab) => (
          <Button
            key={tab.value}
            variant={activeStatus === tab.value ? "default" : "outline"}
            size="sm"
            aria-pressed={activeStatus === tab.value}
            onClick={() => setActiveStatus(tab.value)}
          >
            {tab.label}
          </Button>
        ))}
      </div>

      <div className="mt-6">
        {!loading && !error && orders.length > 0 && (
          <p className="mb-4 text-sm text-muted-foreground">
            {total} order{total !== 1 ? "s" : ""}
          </p>
        )}

        {loading && (
          <div className="space-y-3">
            {Array.from({ length: 4 }).map((_, i) => (
              <OrderCardSkeleton key={i} />
            ))}
          </div>
        )}

        {error && (
          <ErrorState inline message={error} onRetry={loadOrders} homeHref="/home" homeLabel="Back to home" />
        )}

        {!loading && !error && orders.length === 0 && (
          <EmptyState
            icon={Package}
            title={activeStatus ? `No ${getStatusLabel(activeStatus).toLowerCase()} orders` : "No orders yet"}
            description={activeStatus ? "Try another status." : "Orders you place show up here."}
          >
            {activeStatus ? (
              <Button variant="outline" onClick={() => setActiveStatus("")}>
                View all orders
              </Button>
            ) : (
              <Link href="/products" className={cn(buttonVariants({ variant: "outline" }))}>
                Browse products
              </Link>
            )}
          </EmptyState>
        )}

        {!loading && !error && orders.length > 0 && (
          <ul className="space-y-3">
            {orders.map((order) => (
              <li key={order.id}>
                <Link
                  href={`/orders/${order.id}`}
                  className="group/order flex flex-col gap-3 rounded-xl bg-card px-4 py-4 ring-1 ring-foreground/10 transition-shadow hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:flex-row sm:items-center sm:justify-between sm:px-5"
                >
                  <div className="min-w-0 flex-1 space-y-1.5">
                    <div className="flex flex-wrap items-center gap-3">
                      <span className="text-sm font-semibold text-foreground">
                        Order #{order.id.slice(0, 8)}
                      </span>
                      <OrderStatusBadge status={order.status} />
                    </div>
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted-foreground">
                      <span>{formatDate(order.date)}</span>
                      <span>
                        {order.item_count} item{order.item_count !== 1 ? "s" : ""}
                      </span>
                      {order.carrier && (
                        <span className="flex items-center gap-1">
                          <Truck className="size-3.5" aria-hidden />
                          {order.carrier}
                          {order.tracking && (
                            <span className="font-mono text-xs">{order.tracking}</span>
                          )}
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center justify-between gap-3 sm:justify-end">
                    <span className="text-lg font-semibold tabular-nums text-foreground">
                      {formatPrice(order.total)}
                    </span>
                    <ChevronRight className="size-5 text-muted-foreground transition-transform group-hover/order:translate-x-0.5" aria-hidden />
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </PageContainer>
  );
}
