"use client";

import { useState, useCallback } from "react";
import { useRouter, useParams, useSearchParams } from "next/navigation";
import { useLoadEffect } from "@/lib/use-load-effect";
import { useAuth } from "@/lib/auth-context";
import { api, apiUrl, type OrderDetail, type OrderLine as OrderItem, type OrderReturn as ReturnInfo } from "@/lib/api";

type StatusHistoryEntry = OrderDetail["status_history"][number];
import { toastOrderCancelled, toastReturnInitiated } from "@/lib/toast";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { CHIP_CLASS, TONE_BADGE_CLASS } from "@/components/ui/status-badge";
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
    color: TONE_BADGE_CLASS.info,
    dotColor: "bg-info",
    icon: Clock,
    label: "Placed",
  },
  confirmed: {
    color: CHIP_CLASS.primary,
    dotColor: "bg-primary",
    icon: CheckCircle,
    label: "Confirmed",
  },
  shipped: {
    color: TONE_BADGE_CLASS.warning,
    dotColor: "bg-warning",
    icon: Truck,
    label: "Shipped",
  },
  delivered: {
    color: TONE_BADGE_CLASS.success,
    dotColor: "bg-success",
    icon: CheckCircle,
    label: "Delivered",
  },
  returned: {
    color: TONE_BADGE_CLASS.neutral,
    dotColor: "bg-muted-foreground",
    icon: RotateCcw,
    label: "Returned",
  },
  cancelled: {
    color: TONE_BADGE_CLASS.destructive,
    dotColor: "bg-destructive",
    icon: XCircle,
    label: "Cancelled",
  },
};

function getStatusConfig(status: string) {
  const key = status.toLowerCase();
  return (
    STATUS_CONFIG[key] || {
      color: "border-border bg-muted/50 text-muted-foreground",
      dotColor: "bg-muted-foreground",
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
              <div className="absolute left-[11px] top-6 h-[calc(100%-12px)] w-0.5 bg-border" />
            )}

            {/* Dot */}
            <div
              className={`relative z-10 mt-1 size-6 shrink-0 rounded-full border-2 ${
                isLatest
                  ? `${cfg.dotColor} border-card ring-2 ring-offset-1 ring-${cfg.dotColor}`
                  : "border-border bg-card"
              } flex items-center justify-center`}
            >
              {isLatest && (
                <div className="size-2 rounded-full bg-card" />
              )}
              {!isLatest && (
                <div className="size-2 rounded-full bg-muted-foreground" />
              )}
            </div>

            {/* Content */}
            <div className="flex-1 min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span
                  className={`text-sm font-medium ${
                    isLatest ? "text-foreground" : "text-muted-foreground"
                  }`}
                >
                  {cfg.label}
                </span>
                <span className="text-xs text-muted-foreground">
                  {formatTimestamp(entry.timestamp)}
                </span>
              </div>
              {entry.notes && (
                <p className="mt-0.5 text-sm text-muted-foreground">{entry.notes}</p>
              )}
              {entry.location && (
                <p className="mt-0.5 flex items-center gap-1 text-xs text-muted-foreground">
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
        <div className="h-6 w-64 rounded bg-muted" />
        <div className="h-4 w-40 rounded bg-muted" />
      </div>
      <div className="grid gap-8 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <div className="h-48 rounded-xl bg-muted" />
        </div>
        <div className="space-y-4">
          <div className="h-40 rounded-xl bg-muted" />
          <div className="h-32 rounded-xl bg-muted" />
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

  useLoadEffect(loadOrder, Boolean(user && orderId));

  const handleCancelOrder = async () => {
    if (!cancelReason.trim()) return;
    try {
      setCancelling(true);
      await api.cancelOrder(orderId, cancelReason);
      setCancelOpen(false);
      setCancelReason("");
      toastOrderCancelled(orderId);
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
      const result = await api.initiateReturn(orderId, returnReason, refundMethod);
      setReturnOpen(false);
      setReturnReason("");
      setRefundMethod("original_payment");
      toastReturnInitiated(result?.return_id ?? orderId);
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
    <div className="min-h-screen bg-background">
      {/* Header */}
      <div className="border-b border-border bg-card">
        <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
          {/* Back button */}
          <Button
            variant="ghost"
            size="sm"
            className="mb-4 -ml-2 text-muted-foreground hover:text-muted-foreground"
            onClick={() => router.push("/orders")}
          >
            <ArrowLeft className="mr-1.5 size-4" />
            Back to Orders
          </Button>

          {loading && <DetailSkeleton />}
          {error && (
            <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
              {error}
            </div>
          )}

          {!loading && !error && order && statusCfg && (
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <div className="flex flex-wrap items-center gap-3">
                  <h1 className="text-2xl font-bold text-foreground">
                    Order #{order.id}
                  </h1>
                  <Badge variant="outline" className={statusCfg.color}>
                    {statusCfg.label}
                  </Badge>
                </div>
                <p className="mt-1 text-sm text-muted-foreground">
                  Placed on {formatDate(order.date)}
                </p>
              </div>

              {/* Cancel / Return action buttons */}
              <div className="flex items-center gap-2">
                {(order.status === "placed" || order.status === "confirmed") && (
                  <Dialog open={cancelOpen} onOpenChange={setCancelOpen}>
                    <DialogTrigger
                      render={
                        <Button variant="outline" className="border-destructive/30 text-destructive hover:bg-destructive/10 hover:text-destructive">
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
                          className="w-full bg-destructive text-white hover:bg-destructive/90"
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
                        <Button variant="outline" className="border-warning/40 text-[color-mix(in_oklab,var(--warning)_45%,black)] dark:text-warning hover:bg-warning/10">
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
                              className={refundMethod === "original_payment" ? "bg-primary hover:opacity-90" : ""}
                              onClick={() => setRefundMethod("original_payment")}
                            >
                              Original Payment
                            </Button>
                            <Button
                              type="button"
                              variant={refundMethod === "store_credit" ? "default" : "outline"}
                              size="sm"
                              className={refundMethod === "store_credit" ? "bg-primary hover:opacity-90" : ""}
                              onClick={() => setRefundMethod("store_credit")}
                            >
                              Store Credit
                            </Button>
                          </div>
                        </div>
                        <Button
                          className="w-full bg-warning text-warning-foreground hover:bg-warning/90"
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
        <div className="border-b border-success/30 bg-success/10">
          <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-4 sm:px-6 lg:px-8">
            <div className="flex items-center gap-3">
              <CheckCircle className="size-5 text-[color-mix(in_oklab,var(--success)_62%,black)] dark:text-success shrink-0" />
              <div>
                <p className="font-medium text-[color-mix(in_oklab,var(--success)_62%,black)] dark:text-success">
                  Order placed successfully!
                </p>
                <p className="text-sm text-[color-mix(in_oklab,var(--success)_62%,black)] dark:text-success">
                  Your order is being processed.
                </p>
              </div>
            </div>
            <Button
              variant="outline"
              size="sm"
              className="shrink-0 border-success/40 text-[color-mix(in_oklab,var(--success)_62%,black)] dark:text-success hover:bg-success/15"
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
                          className="cursor-pointer hover:bg-accent"
                          onClick={() =>
                            router.push(`/products/${item.product_id}`)
                          }
                        >
                          <TableCell>
                            <img
                              src={productImageUrl(item.product_id, 48, 48, item.image_url, item.category)}
                              alt={item.name}
                              className="size-10 rounded-md object-cover bg-muted"
                              loading="lazy"
                            />
                          </TableCell>
                          <TableCell className="font-medium text-foreground">
                            {item.name}
                          </TableCell>
                          <TableCell>
                            <Badge
                              variant="outline"
                              className="text-xs font-normal"
                            >
                              {item.category}
                            </Badge>
                          </TableCell>
                          <TableCell className="text-right">
                            {item.quantity}
                          </TableCell>
                          <TableCell className="text-right text-muted-foreground">
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
                      <MapPin className="size-4 text-muted-foreground" />
                      Shipping
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <div className="text-sm text-muted-foreground">
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
                            <Truck className="size-4 text-muted-foreground" />
                            <span className="font-medium text-muted-foreground">
                              {order.carrier}
                            </span>
                          </div>
                          {order.tracking && (
                            <p className="font-mono text-xs text-muted-foreground pl-6">
                              {order.tracking}
                            </p>
                          )}
                        </div>
                      </>
                    )}

                    {/* Billing Address */}
                    <Separator />
                    <div className="space-y-1">
                      <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                        Billing Address
                      </p>
                      {order.billing_address &&
                       (order.billing_address.street !== order.shipping_address.street ||
                        order.billing_address.city !== order.shipping_address.city ||
                        order.billing_address.state !== order.shipping_address.state ||
                        order.billing_address.zip !== order.shipping_address.zip) ? (
                        <div className="text-sm text-muted-foreground">
                          {order.billing_address.name && (
                            <p className="font-medium text-muted-foreground">{order.billing_address.name}</p>
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
                        <p className="text-sm text-muted-foreground italic">
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
                    <span className="text-muted-foreground">Subtotal</span>
                    <span className="text-muted-foreground">
                      {formatPrice(order.items?.reduce((sum: number, i: OrderItem) => sum + i.subtotal, 0) ?? 0)}
                    </span>
                  </div>
                  {order.discount > 0 && (
                    <div className="flex items-center justify-between text-sm">
                      <span className="text-muted-foreground">Discount</span>
                      <span className="text-[color-mix(in_oklab,var(--success)_62%,black)] dark:text-success">
                        -{formatPrice(order.discount)}
                      </span>
                    </div>
                  )}
                  <Separator />
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium text-muted-foreground">
                      Total
                    </span>
                    <span className="text-lg font-bold text-foreground">
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
                      <RotateCcw className="size-4 text-[color-mix(in_oklab,var(--warning)_45%,black)] dark:text-warning" />
                      Return Information
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <div className="space-y-2">
                      <div className="flex items-center justify-between text-sm">
                        <span className="text-muted-foreground">Reason</span>
                        <span className="text-muted-foreground">
                          {(order["return"] as ReturnInfo).reason}
                        </span>
                      </div>
                      <div className="flex items-center justify-between text-sm">
                        <span className="text-muted-foreground">Status</span>
                        <Badge
                          variant="outline"
                          className="border-warning/30 bg-warning/10 text-[color-mix(in_oklab,var(--warning)_45%,black)] dark:text-warning"
                        >
                          {(order["return"] as ReturnInfo).status}
                        </Badge>
                      </div>
                      <div className="flex items-center justify-between text-sm">
                        <span className="text-muted-foreground">Refund Method</span>
                        <span className="text-muted-foreground">
                          {(order["return"] as ReturnInfo).refund_method}
                        </span>
                      </div>
                      {(order["return"] as ReturnInfo).refund_amount != null && (
                        <div className="flex items-center justify-between text-sm">
                          <span className="text-muted-foreground">Refund Amount</span>
                          <span className="font-medium text-[color-mix(in_oklab,var(--success)_62%,black)] dark:text-success">
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
                            <div className="flex items-center justify-between text-xs text-muted-foreground">
                              <span>Return initiated</span>
                              <span>
                                {formatDate((order["return"] as ReturnInfo).created_at!)}
                              </span>
                            </div>
                          )}
                          {(order["return"] as ReturnInfo).resolved_at && (
                            <div className="flex items-center justify-between text-xs text-muted-foreground">
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
                            className="w-full border-warning/40 text-[color-mix(in_oklab,var(--warning)_45%,black)] dark:text-warning hover:bg-warning/10"
                            onClick={() => {
                              const url = (order["return"] as ReturnInfo).return_label_url || "";
                              window.open(apiUrl(url), "_blank");
                            }}
                          >
                            <Download className="mr-2 size-4" />
                            Download Return Label
                          </Button>
                          <p className="text-xs text-muted-foreground text-center">
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
