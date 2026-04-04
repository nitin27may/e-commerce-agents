"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
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
  ShoppingCart,
  Info,
  Star,
  ArrowRight,
  Loader2,
} from "lucide-react";
import { productImageUrl } from "@/lib/images";
import { formatPrice, formatDate } from "@/lib/format";
import { OrderStatusBadge } from "@/components/status-badge";

export default function SellerDashboardPage() {
  const router = useRouter();
  const { user, isLoading: authLoading, isAdmin } = useAuth();

  const [products, setProducts] = useState<any[]>([]);
  const [orders, setOrders] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const isSeller = user?.role === "seller" || isAdmin;

  const loadData = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const [productsRes, ordersRes] = await Promise.all([
        api.getProducts(),
        api.getOrders(),
      ]);
      setProducts(productsRes.products);
      setOrders(ordersRes.orders);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to load dashboard data",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (user && isSeller) loadData();
  }, [user, isSeller, loadData]);

  if (authLoading) return null;

  if (!user || !isSeller) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="text-center">
          <Package className="mx-auto size-12 text-slate-300" />
          <h2 className="mt-4 text-lg font-semibold text-slate-800">
            Access Denied
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            The seller dashboard is only available to sellers and admins.
          </p>
        </div>
      </div>
    );
  }

  const recentOrders = orders.slice(0, 5);
  const recentProducts = products.slice(0, 5);

  return (
    <div className="min-h-screen bg-slate-50">
      {/* Header */}
      <div className="border-b border-slate-200 bg-white">
        <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
          <div className="flex items-center gap-3">
            <div className="flex size-10 items-center justify-center rounded-lg bg-teal-600">
              <BarChart3 className="size-5 text-white" />
            </div>
            <div>
              <h1 className="text-2xl font-bold text-slate-900">
                Seller Dashboard
              </h1>
              <p className="text-sm text-slate-500">
                Manage your products and track orders
              </p>
            </div>
          </div>
        </div>
      </div>

      <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        {/* Preview banner */}
        <div className="mb-6 flex items-start gap-3 rounded-lg border border-blue-200 bg-blue-50 px-4 py-3">
          <Info className="mt-0.5 size-4 shrink-0 text-blue-600" />
          <div>
            <p className="text-sm font-medium text-blue-800">
              Seller Dashboard (Preview)
            </p>
            <p className="mt-0.5 text-xs text-blue-600">
              This is a read-only preview of seller capabilities. Full seller
              features including product creation, inventory management, and
              order fulfillment are coming soon.
            </p>
          </div>
        </div>

        {/* Loading */}
        {loading && (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="size-6 animate-spin text-slate-400" />
          </div>
        )}

        {/* Error */}
        {error && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        )}

        {!loading && !error && (
          <>
            {/* Summary cards */}
            <div className="mb-8 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
              <Card>
                <CardHeader>
                  <CardDescription>Total Products</CardDescription>
                  <CardTitle className="text-3xl">{products.length}</CardTitle>
                </CardHeader>
                <CardContent>
                  <Link href="/seller/products">
                    <Button variant="outline" size="sm">
                      View All <ArrowRight className="ml-1 size-3" />
                    </Button>
                  </Link>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardDescription>Recent Orders</CardDescription>
                  <CardTitle className="text-3xl">{orders.length}</CardTitle>
                </CardHeader>
                <CardContent>
                  <Link href="/orders">
                    <Button variant="outline" size="sm">
                      View All <ArrowRight className="ml-1 size-3" />
                    </Button>
                  </Link>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardDescription>Avg. Product Rating</CardDescription>
                  <CardTitle className="flex items-center gap-2 text-3xl">
                    {products.length > 0
                      ? (
                          products.reduce(
                            (sum: number, p: any) => sum + (p.rating || 0),
                            0,
                          ) / products.length
                        ).toFixed(1)
                      : "N/A"}
                    {products.length > 0 && (
                      <Star className="size-6 fill-amber-400 text-amber-400" />
                    )}
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="text-xs text-slate-500">
                    Across {products.length} products
                  </p>
                </CardContent>
              </Card>
            </div>

            {/* Recent products table */}
            <div className="mb-8">
              <div className="mb-4 flex items-center justify-between">
                <h2 className="text-lg font-semibold text-slate-800">
                  Products
                </h2>
                <Link href="/seller/products">
                  <Button variant="outline" size="sm">
                    View All
                  </Button>
                </Link>
              </div>
              <Card>
                <CardContent className="p-0">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead className="w-[60px]">Image</TableHead>
                        <TableHead>Product</TableHead>
                        <TableHead>Category</TableHead>
                        <TableHead className="text-right">Price</TableHead>
                        <TableHead className="text-right">Rating</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {recentProducts.map((product: any) => (
                        <TableRow
                          key={product.id}
                          className="cursor-pointer"
                          onClick={() =>
                            router.push(`/products/${product.id}`)
                          }
                        >
                          <TableCell>
                            <img
                              src={productImageUrl(product.id, 48, 48)}
                              alt={product.name}
                              className="size-10 rounded-md object-cover bg-slate-100"
                            />
                          </TableCell>
                          <TableCell>
                            <div>
                              <p className="font-medium text-slate-800">
                                {product.name}
                              </p>
                              <p className="text-xs text-slate-500">
                                {product.brand}
                              </p>
                            </div>
                          </TableCell>
                          <TableCell>
                            <Badge variant="outline" className="text-[10px]">
                              {product.category}
                            </Badge>
                          </TableCell>
                          <TableCell className="text-right font-medium">
                            {formatPrice(product.price)}
                          </TableCell>
                          <TableCell className="text-right">
                            <span className="flex items-center justify-end gap-1 text-xs">
                              <Star className="size-3 fill-amber-400 text-amber-400" />
                              {product.rating?.toFixed(1) ?? "N/A"}
                            </span>
                          </TableCell>
                        </TableRow>
                      ))}
                      {recentProducts.length === 0 && (
                        <TableRow>
                          <TableCell colSpan={5} className="py-8 text-center text-sm text-slate-500">
                            No products found.
                          </TableCell>
                        </TableRow>
                      )}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
            </div>

            {/* Recent orders table */}
            <div>
              <div className="mb-4 flex items-center justify-between">
                <h2 className="text-lg font-semibold text-slate-800">
                  Recent Orders
                </h2>
                <Link href="/orders">
                  <Button variant="outline" size="sm">
                    View All
                  </Button>
                </Link>
              </div>
              <Card>
                <CardContent className="p-0">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Order ID</TableHead>
                        <TableHead>Status</TableHead>
                        <TableHead>Date</TableHead>
                        <TableHead>Items</TableHead>
                        <TableHead className="text-right">Total</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {recentOrders.map((order: any) => (
                        <TableRow
                          key={order.id}
                          className="cursor-pointer"
                          onClick={() => router.push(`/orders/${order.id}`)}
                        >
                          <TableCell className="font-mono text-xs text-slate-600">
                            #{order.id?.slice(0, 8)}
                          </TableCell>
                          <TableCell>
                            <OrderStatusBadge status={order.status} />
                          </TableCell>
                          <TableCell className="text-xs text-slate-500">
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
                          <TableCell colSpan={5} className="py-8 text-center text-sm text-slate-500">
                            No orders found.
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
      </div>
    </div>
  );
}
