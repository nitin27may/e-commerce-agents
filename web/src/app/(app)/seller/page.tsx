"use client";

import { useState, useCallback } from "react";
import Link from "next/link";
import { useLoadEffect } from "@/lib/use-load-effect";
import { useAuth } from "@/lib/auth-context";
import { api, type SellerOrder, type SellerStats } from "@/lib/api";
import { asArray, asNumber } from "@/lib/guards";
import { ErrorState } from "@/components/error-state";
import { Card, CardContent } from "@/components/ui/card";
import { StatCard } from "@/components/ui/stat-card";
import { PageContainer, PageHeader } from "@/components/ui/page-header";
import { AccessDenied } from "@/components/ui/access-denied";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableHeader,
  TableBody,
  TableHead,
  TableRow,
  TableCell,
} from "@/components/ui/table";
import {
  BarChart3,
  Package,
  DollarSign,
  ShoppingCart,
  Star,
  Loader2,
} from "lucide-react";
import { formatPrice, formatDate } from "@/lib/format";
import { OrderStatusBadge } from "@/components/status-badge";

export default function SellerDashboardPage() {
  const { user, isLoading: authLoading, isAdmin } = useAuth();

  const [stats, setStats] = useState<SellerStats | null>(null);
  const [orders, setOrders] = useState<SellerOrder[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const isSeller = user?.role === "seller" || isAdmin;

  const loadData = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const [statsRes, ordersRes] = await Promise.all([
        api.getSellerStats(),
        api.getSellerOrders(),
      ]);
      // An empty or reshaped response must not reach the render as undefined fields.
      setStats({
        ...(statsRes && !Array.isArray(statsRes) ? statsRes : {}),
        product_count: asNumber(statsRes?.product_count),
        total_revenue: asNumber(statsRes?.total_revenue),
        order_count: asNumber(statsRes?.order_count),
        avg_rating: asNumber(statsRes?.avg_rating),
      });
      setOrders(asArray(ordersRes?.orders));
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to load dashboard data",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useLoadEffect(loadData, Boolean(user && isSeller));

  if (authLoading) return null;

  if (!user || !isSeller) {
    return <AccessDenied description="The seller dashboard is only available to sellers and admins." />;
  }

  const recentOrders = orders.slice(0, 5);

  return (
    <PageContainer>
      <PageHeader
        icon={BarChart3}
        title="Seller dashboard"
        description="Your catalogue, revenue and recent orders"
        actions={
          <Button variant="outline" render={<Link href="/seller/products" />}>
            <Package /> Manage products
          </Button>
        }
      />

        {loading && (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="size-6 animate-spin text-muted-foreground" />
          </div>
        )}

        {error && (
          <ErrorState inline onRetry={loadData} message={error} homeHref="/home" homeLabel="Back to dashboard" />
        )}

        {!loading && !error && stats && (
          <>
            <div className="mb-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <StatCard
                label="Products"
                value={stats.product_count}
                icon={Package}
                hint="Listed in the store"
              />
              <StatCard
                label="Revenue"
                value={formatPrice(stats.total_revenue)}
                icon={DollarSign}
                hint={`Across ${stats.order_count} orders`}
              />
              <StatCard
                label="Orders"
                value={stats.order_count}
                icon={ShoppingCart}
                hint="Orders containing your products"
              />
              <StatCard
                label="Avg. rating"
                value={stats.avg_rating > 0 ? stats.avg_rating.toFixed(1) : "N/A"}
                icon={Star}
                hint={`Across ${stats.product_count} products`}
              />
            </div>

            {/* Recent orders table */}
            <div>
              <h2 className="mb-3 text-lg font-semibold text-foreground">
                Recent orders
              </h2>
              <Card>
                <CardContent className="p-0">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Order</TableHead>
                        <TableHead>Buyer</TableHead>
                        <TableHead>Status</TableHead>
                        <TableHead>Date</TableHead>
                        <TableHead>Items</TableHead>
                        <TableHead className="text-right">Total</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {recentOrders.map((order) => (
                        <TableRow key={order.id}>
                          <TableCell className="font-mono text-xs text-muted-foreground">
                            #{order.id?.slice(0, 8)}
                          </TableCell>
                          <TableCell>
                            <div>
                              <p className="text-sm font-medium text-foreground">
                                {order.buyer_name}
                              </p>
                              <p className="text-xs text-muted-foreground">
                                {order.buyer_email}
                              </p>
                            </div>
                          </TableCell>
                          <TableCell>
                            <OrderStatusBadge status={order.status} />
                          </TableCell>
                          <TableCell className="text-xs text-muted-foreground">
                            {formatDate(order.date)}
                          </TableCell>
                          <TableCell className="text-xs">
                            {order.item_count} item
                            {order.item_count !== 1 ? "s" : ""}
                          </TableCell>
                          <TableCell className="text-right font-medium">
                            {formatPrice(order.total)}
                          </TableCell>
                        </TableRow>
                      ))}
                      {recentOrders.length === 0 && (
                        <TableRow>
                          <TableCell
                            colSpan={6}
                            className="py-8 text-center text-sm text-muted-foreground"
                          >
                            No orders found for your products.
                          </TableCell>
                        </TableRow>
                      )}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
            </div>
          </>
        )}
    </PageContainer>
  );
}
