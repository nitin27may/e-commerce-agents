"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter, useParams, useSearchParams } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
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
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Table,
  TableHeader,
  TableBody,
  TableHead,
  TableRow,
  TableCell,
} from "@/components/ui/table";
import {
  ArrowLeft,
  Package,
  Truck,
  Clock,
  CheckCircle,
  XCircle,
  RotateCcw,
  MapPin,
  Loader2,
  Download,
  Ban,
  AlertCircle,
} from "lucide-react";
import { productImageUrl } from "@/lib/images";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface StatusHistoryEntry {
  status: string;
  timestamp: string;
  notes?: string;
  location?: string;
}

interface OrderItem {
  product_id: string;
  name: string;
  category: string;
  image_url?: string;
  quantity: number;
  unit_price: number;
  subtotal: number;
}

interface ShippingInfo {
  street: string;
  city: string;
  state: string;
  zip: string;
  carrier?: string;
  tracking_number?: string;
}

interface ReturnInfo {
  id: string;
  reason: string;
  status: string;
  refund_method: string;
  refund_amount: number | null;
  created_at?: string;
  resolved_at?: string | null;
  return_label_url?: string;
}

interface BillingAddress {
  street: string;
  city: string;
  state: string;
  zip: string;
  name?: string;
  country?: string;
}

interface OrderDetail {
  id: string;
  date: string;
  status: string;
  items: OrderItem[];
  status_history: StatusHistoryEntry[];
  shipping_address: ShippingInfo;
  billing_address?: BillingAddress | null;
  carrier?: string;
  tracking?: string;
  discount: number;
  total: number;
  return?: ReturnInfo | null;
  [key: string]: unknown;
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

function formatTimestamp(dateStr: string): string {
  try {
    return new Date(dateStr).toLocaleString("en-US", {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return dateStr;
  }
}

const STATUS_CONFIG: Record<
  string,
  { color: string; dotColor: string; icon: React.ElementType; label: string }
> = {
  placed: {
    color: "border-blue-200 bg-blue-50 text-blue-700",
    dotColor: "bg-blue-500",
    icon: Clock,
    label: "Placed",
  },
  confirmed: {
    color: "border-indigo-200 bg-indigo-50 text-indigo-700",
    dotColor: "bg-indigo-500",
    icon: CheckCircle,
    label: "Confirmed",
  },
  shipped: {
    color: "border-amber-200 bg-amber-50 text-amber-700",
    dotColor: "bg-amber-500",
    icon: Truck,
    label: "Shipped",
  },
  delivered: {
    color: "border-emerald-200 bg-emerald-50 text-emerald-700",
    dotColor: "bg-emerald-500",
    icon: CheckCircle,
    label: "Delivered",
  },
  returned: {
    color: "border-orange-200 bg-orange-50 text-orange-700",
    dotColor: "bg-orange-500",
    icon: RotateCcw,
    label: "Returned",
  },
  cancelled: {
    color: "border-red-200 bg-red-50 text-red-700",
    dotColor: "bg-red-500",
    icon: XCircle,
    label: "Cancelled",
  },
};

function getStatusConfig(status: string) {
  const key = status.toLowerCase();
  return (
    STATUS_CONFIG[key] || {
      color: "border-slate-200 bg-slate-50 text-slate-700",
      dotColor: "bg-slate-500",
      icon: Package,
      label: status,
    }
  );
}

// ---------------------------------------------------------------------------
// Status Timeline
// ---------------------------------------------------------------------------

function StatusTimeline({ history }: { history: StatusHistoryEntry[] }) {
  if (!history || history.length === 0) return null;

  return (
    <div className="relative space-y-0">
      {history.map((entry, idx) => {
        const isLatest = idx === history.length - 1;
        const isLast = idx === history.length - 1;
        const cfg = getStatusConfig(entry.status);

        return (
          <div key={idx} className="relative flex gap-4 pb-6 last:pb-0">
            {/* Vertical line */}
            {!isLast && (
              <div className="absolute left-[11px] top-6 h-[calc(100%-12px)] w-0.5 bg-slate-200" />
            )}

            {/* Dot */}
            <div
              className={`relative z-10 mt-1 size-6 shrink-0 rounded-full border-2 ${
                isLatest
                  ? `${cfg.dotColor} border-white ring-2 ring-offset-1 ring-${cfg.dotColor}`
                  : "border-slate-200 bg-white"
              } flex items-center justify-center`}
            >
              {isLatest && (
                <div className="size-2 rounded-full bg-white" />
              )}
              {!isLatest && (
                <div className="size-2 rounded-full bg-slate-300" />
              )}
            </div>

            {/* Content */}
            <div className="flex-1 min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span
                  className={`text-sm font-medium ${
                    isLatest ? "text-slate-900" : "text-slate-600"
                  }`}
                >
                  {cfg.label}
                </span>
                <span className="text-xs text-slate-400">
                  {formatTimestamp(entry.timestamp)}
                </span>
              </div>
              {entry.notes && (
                <p className="mt-0.5 text-sm text-slate-500">{entry.notes}</p>
              )}
              {entry.location && (
                <p className="mt-0.5 flex items-center gap-1 text-xs text-slate-400">
                  <MapPin className="size-3" />
                  {entry.location}
                </p>
              )}
            </div>
          </div>
        );
      })}
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
        <div className="h-6 w-64 rounded bg-slate-200" />
        <div className="h-4 w-40 rounded bg-slate-200" />
      </div>
      <div className="grid gap-8 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <div className="h-48 rounded-xl bg-slate-200" />
        </div>
        <div className="space-y-4">
          <div className="h-40 rounded-xl bg-slate-200" />
          <div className="h-32 rounded-xl bg-slate-200" />
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function OrderDetailPage() {
  const router = useRouter();
  const params = useParams();
  const searchParams = useSearchParams();
  const { user, isLoading: authLoading } = useAuth();

  const justPlaced = searchParams.get("placed") === "true";

  const [order, setOrder] = useState<OrderDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Cancel order state
  const [cancelOpen, setCancelOpen] = useState(false);
  const [cancelReason, setCancelReason] = useState("");
  const [cancelling, setCancelling] = useState(false);

  // Return order state
  const [returnOpen, setReturnOpen] = useState(false);
  const [returnReason, setReturnReason] = useState("");
  const [refundMethod, setRefundMethod] = useState<"original_payment" | "store_credit">("original_payment");
  const [returning, setReturning] = useState(false);

  const orderId = params.id as string;

  const loadOrder = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await api.getOrder(orderId);
      setOrder(data);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to load order"
      );
    } finally {
      setLoading(false);
    }
  }, [orderId]);

  useEffect(() => {
    if (user && orderId) loadOrder();
  }, [user, orderId, loadOrder]);

  const handleCancelOrder = async () => {
    if (!cancelReason.trim()) return;
    try {
      setCancelling(true);
      await api.cancelOrder(orderId, cancelReason);
      setCancelOpen(false);
      setCancelReason("");
      await loadOrder();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to cancel order");
    } finally {
      setCancelling(false);
    }
  };

  const handleReturnOrder = async () => {
    if (!returnReason.trim()) return;
    try {
      setReturning(true);
      await api.initiateReturn(orderId, returnReason, refundMethod);
      setReturnOpen(false);
      setReturnReason("");
      setRefundMethod("original_payment");
      await loadOrder();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to initiate return");
    } finally {
      setReturning(false);
    }
  };

  if (authLoading || !user) return null;

  const statusCfg = order ? getStatusConfig(order.status) : null;

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
            onClick={() => router.push("/orders")}
          >
            <ArrowLeft className="mr-1.5 size-4" />
            Back to Orders
          </Button>

          {loading && <DetailSkeleton />}
          {error && (
            <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              {error}
            </div>
          )}

          {!loading && !error && order && statusCfg && (
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <div className="flex flex-wrap items-center gap-3">
                  <h1 className="text-2xl font-bold text-slate-900">
                    Order #{order.id}
                  </h1>
                  <Badge variant="outline" className={statusCfg.color}>
                    {statusCfg.label}
                  </Badge>
                </div>
                <p className="mt-1 text-sm text-slate-500">
                  Placed on {formatDate(order.date)}
                </p>
              </div>

              {/* Cancel / Return action buttons */}
              <div className="flex items-center gap-2">
                {(order.status === "placed" || order.status === "confirmed") && (
                  <Dialog open={cancelOpen} onOpenChange={setCancelOpen}>
                    <DialogTrigger
                      render={
                        <Button variant="outline" className="border-red-200 text-red-600 hover:bg-red-50 hover:text-red-700">
                          <Ban className="mr-1.5 size-4" />
                          Cancel Order
                        </Button>
                      }
                    />
                    <DialogContent>
                      <DialogHeader>
                        <DialogTitle>Cancel Order</DialogTitle>
                      </DialogHeader>
                      <div className="space-y-4 pt-2">
                        <div className="space-y-2">
                          <Label htmlFor="cancel-reason">Reason for cancellation</Label>
                          <Textarea
                            id="cancel-reason"
                            placeholder="Tell us why you want to cancel this order..."
                            value={cancelReason}
                            onChange={(e) => setCancelReason(e.target.value)}
                            rows={3}
                          />
                        </div>
                        <Button
                          className="w-full bg-red-600 text-white hover:bg-red-700"
                          disabled={cancelling || !cancelReason.trim()}
                          onClick={handleCancelOrder}
                        >
                          {cancelling && <Loader2 className="mr-2 size-4 animate-spin" />}
                          Confirm Cancellation
                        </Button>
                      </div>
                    </DialogContent>
                  </Dialog>
                )}

                {order.status === "delivered" && !order["return"] && (
                  <Dialog open={returnOpen} onOpenChange={setReturnOpen}>
                    <DialogTrigger
                      render={
                        <Button variant="outline" className="border-orange-200 text-orange-600 hover:bg-orange-50 hover:text-orange-700">
                          <RotateCcw className="mr-1.5 size-4" />
                          Return Order
                        </Button>
                      }
                    />
                    <DialogContent>
                      <DialogHeader>
                        <DialogTitle>Return Order</DialogTitle>
                      </DialogHeader>
                      <div className="space-y-4 pt-2">
                        <div className="space-y-2">
                          <Label htmlFor="return-reason">Reason for return</Label>
                          <Textarea
                            id="return-reason"
                            placeholder="Tell us why you want to return this order..."
                            value={returnReason}
                            onChange={(e) => setReturnReason(e.target.value)}
                            rows={3}
                          />
                        </div>
                        <div className="space-y-2">
                          <Label>Refund method</Label>
                          <div className="flex gap-2">
                            <Button
                              type="button"
                              variant={refundMethod === "original_payment" ? "default" : "outline"}
                              size="sm"
                              className={refundMethod === "original_payment" ? "bg-teal-600 hover:bg-teal-700" : ""}
                              onClick={() => setRefundMethod("original_payment")}
                            >
                              Original Payment
                            </Button>
                            <Button
                              type="button"
                              variant={refundMethod === "store_credit" ? "default" : "outline"}
                              size="sm"
                              className={refundMethod === "store_credit" ? "bg-teal-600 hover:bg-teal-700" : ""}
                              onClick={() => setRefundMethod("store_credit")}
                            >
                              Store Credit
                            </Button>
                          </div>
                        </div>
                        <Button
                          className="w-full bg-orange-600 text-white hover:bg-orange-700"
                          disabled={returning || !returnReason.trim()}
                          onClick={handleReturnOrder}
                        >
                          {returning && <Loader2 className="mr-2 size-4 animate-spin" />}
                          Submit Return
                        </Button>
                      </div>
                    </DialogContent>
                  </Dialog>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Order Placed Confirmation Banner */}
      {justPlaced && !loading && !error && order && (
        <div className="border-b border-emerald-200 bg-emerald-50">
          <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-4 sm:px-6 lg:px-8">
            <div className="flex items-center gap-3">
              <CheckCircle className="size-5 text-emerald-600 shrink-0" />
              <div>
                <p className="font-medium text-emerald-800">
                  Order placed successfully!
                </p>
                <p className="text-sm text-emerald-600">
                  Your order is being processed.
                </p>
              </div>
            </div>
            <Button
              variant="outline"
              size="sm"
              className="shrink-0 border-emerald-300 text-emerald-700 hover:bg-emerald-100"
              onClick={() => router.push("/products")}
            >
              Continue Shopping
            </Button>
          </div>
        </div>
      )}

      {/* Content */}
      {!loading && !error && order && (
        <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
          <div className="grid gap-8 lg:grid-cols-3">
            {/* Left column: timeline, items */}
            <div className="space-y-8 lg:col-span-2">
              {/* Status Timeline */}
              {order.status_history && order.status_history.length > 0 && (
                <Card>
                  <CardHeader>
                    <CardTitle>Order Status</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <StatusTimeline history={order.status_history} />
                  </CardContent>
                </Card>
              )}

              {/* Order Items */}
              <Card>
                <CardHeader>
                  <CardTitle>
                    Items ({order.items.length})
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <Table>
                    <TableHeader>
                      <TableRow className="hover:bg-transparent">
                        <TableHead className="w-12"></TableHead>
                        <TableHead>Product</TableHead>
                        <TableHead>Category</TableHead>
                        <TableHead className="text-right">Qty</TableHead>
                        <TableHead className="text-right">
                          Unit Price
                        </TableHead>
                        <TableHead className="text-right">
                          Subtotal
                        </TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {order.items.map((item, idx) => (
                        <TableRow
                          key={idx}
                          className="cursor-pointer hover:bg-slate-50"
                          onClick={() =>
                            router.push(`/products/${item.product_id}`)
                          }
                        >
                          <TableCell>
                            <img
                              src={productImageUrl(item.product_id, 48, 48, item.image_url, item.category)}
                              alt={item.name}
                              className="size-10 rounded-md object-cover bg-slate-100"
                              loading="lazy"
                            />
                          </TableCell>
                          <TableCell className="font-medium text-slate-800">
                            {item.name}
                          </TableCell>
                          <TableCell>
                            <Badge
                              variant="outline"
                              className="text-[10px] font-normal"
                            >
                              {item.category}
                            </Badge>
                          </TableCell>
                          <TableCell className="text-right">
                            {item.quantity}
                          </TableCell>
                          <TableCell className="text-right text-slate-600">
                            {formatPrice(item.unit_price)}
                          </TableCell>
                          <TableCell className="text-right font-medium">
                            {formatPrice(item.subtotal)}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
            </div>

            {/* Right column: shipping, summary, return */}
            <div className="space-y-6">
              {/* Shipping Info */}
              {order.shipping_address && (
                <Card>
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                      <MapPin className="size-4 text-slate-400" />
                      Shipping
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <div className="text-sm text-slate-600">
                      <p>{order.shipping_address.street}</p>
                      <p>
                        {order.shipping_address.city}, {order.shipping_address.state}{" "}
                        {order.shipping_address.zip}
                      </p>
                    </div>
                    {order.carrier && (
                      <>
                        <Separator />
                        <div className="space-y-1.5">
                          <div className="flex items-center gap-2 text-sm">
                            <Truck className="size-4 text-slate-400" />
                            <span className="font-medium text-slate-700">
                              {order.carrier}
                            </span>
                          </div>
                          {order.tracking && (
                            <p className="font-mono text-xs text-slate-500 pl-6">
                              {order.tracking}
                            </p>
                          )}
                        </div>
                      </>
                    )}

                    {/* Billing Address */}
                    <Separator />
                    <div className="space-y-1">
                      <p className="text-xs font-medium text-slate-500 uppercase tracking-wider">
                        Billing Address
                      </p>
                      {order.billing_address &&
                       (order.billing_address.street !== order.shipping_address.street ||
                        order.billing_address.city !== order.shipping_address.city ||
                        order.billing_address.state !== order.shipping_address.state ||
                        order.billing_address.zip !== order.shipping_address.zip) ? (
                        <div className="text-sm text-slate-600">
                          {order.billing_address.name && (
                            <p className="font-medium text-slate-700">{order.billing_address.name}</p>
                          )}
                          <p>{order.billing_address.street}</p>
                          <p>
                            {order.billing_address.city}, {order.billing_address.state}{" "}
                            {order.billing_address.zip}
                          </p>
                          {order.billing_address.country && (
                            <p>{order.billing_address.country}</p>
                          )}
                        </div>
                      ) : (
                        <p className="text-sm text-slate-500 italic">
                          Same as shipping address
                        </p>
                      )}
                    </div>
                  </CardContent>
                </Card>
              )}

              {/* Order Summary */}
              <Card>
                <CardHeader>
                  <CardTitle>Order Summary</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2">
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-slate-500">Subtotal</span>
                    <span className="text-slate-700">
                      {formatPrice(order.items?.reduce((sum: number, i: OrderItem) => sum + i.subtotal, 0) ?? 0)}
                    </span>
                  </div>
                  {order.discount > 0 && (
                    <div className="flex items-center justify-between text-sm">
                      <span className="text-slate-500">Discount</span>
                      <span className="text-emerald-600">
                        -{formatPrice(order.discount)}
                      </span>
                    </div>
                  )}
                  <Separator />
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium text-slate-700">
                      Total
                    </span>
                    <span className="text-lg font-bold text-slate-900">
                      {formatPrice(order.total)}
                    </span>
                  </div>
                </CardContent>
              </Card>

              {/* Return Info */}
              {order["return"] && (
                <Card>
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                      <RotateCcw className="size-4 text-orange-500" />
                      Return Information
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <div className="space-y-2">
                      <div className="flex items-center justify-between text-sm">
                        <span className="text-slate-500">Reason</span>
                        <span className="text-slate-700">
                          {(order["return"] as ReturnInfo).reason}
                        </span>
                      </div>
                      <div className="flex items-center justify-between text-sm">
                        <span className="text-slate-500">Status</span>
                        <Badge
                          variant="outline"
                          className="border-orange-200 bg-orange-50 text-orange-700"
                        >
                          {(order["return"] as ReturnInfo).status}
                        </Badge>
                      </div>
                      <div className="flex items-center justify-between text-sm">
                        <span className="text-slate-500">Refund Method</span>
                        <span className="text-slate-700">
                          {(order["return"] as ReturnInfo).refund_method}
                        </span>
                      </div>
                      {(order["return"] as ReturnInfo).refund_amount != null && (
                        <div className="flex items-center justify-between text-sm">
                          <span className="text-slate-500">Refund Amount</span>
                          <span className="font-medium text-emerald-600">
                            {formatPrice((order["return"] as ReturnInfo).refund_amount!)}
                          </span>
                        </div>
                      )}
                    </div>
                    {((order["return"] as ReturnInfo).created_at ||
                      (order["return"] as ReturnInfo).resolved_at) && (
                      <>
                        <Separator />
                        <div className="space-y-1.5">
                          {(order["return"] as ReturnInfo).created_at && (
                            <div className="flex items-center justify-between text-xs text-slate-500">
                              <span>Return initiated</span>
                              <span>
                                {formatDate((order["return"] as ReturnInfo).created_at!)}
                              </span>
                            </div>
                          )}
                          {(order["return"] as ReturnInfo).resolved_at && (
                            <div className="flex items-center justify-between text-xs text-slate-500">
                              <span>Refund processed</span>
                              <span>
                                {formatDate((order["return"] as ReturnInfo).resolved_at!)}
                              </span>
                            </div>
                          )}
                        </div>
                      </>
                    )}

                    {/* Return Label Download */}
                    {(order["return"] as ReturnInfo).return_label_url && (
                      <>
                        <Separator />
                        <div className="space-y-2">
                          <Button
                            variant="outline"
                            className="w-full border-orange-200 text-orange-700 hover:bg-orange-50"
                            onClick={() => {
                              const url = (order["return"] as ReturnInfo).return_label_url || "";
                              const resolved = url.startsWith("/api")
                                ? `${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080"}${url}`
                                : url;
                              window.open(resolved, "_blank");
                            }}
                          >
                            <Download className="mr-2 size-4" />
                            Download Return Label
                          </Button>
                          <p className="text-xs text-slate-500 text-center">
                            Print the return label and drop off at any carrier location
                          </p>
                        </div>
                      </>
                    )}
                  </CardContent>
                </Card>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
