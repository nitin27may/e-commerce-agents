"use client";

import { useState, useCallback } from "react";
import { useRouter, useParams, useSearchParams } from "next/navigation";
import { useLoadEffect } from "@/lib/use-load-effect";
import { useAuth } from "@/lib/auth-context";
import { api, apiUrl, type OrderDetail, type OrderLine as OrderItem, type OrderReturn as ReturnInfo } from "@/lib/api";

type StatusHistoryEntry = OrderDetail["status_history"][number];
import { toastOrderCancelled, toastReturnInitiated } from "@/lib/toast";
import Link from "next/link";
import { Button, buttonVariants } from "@/components/ui/button";
import { StatusBadge, TONE_BADGE_CLASS, TONE_TEXT_CLASS } from "@/components/ui/status-badge";
import { PageContainer, PageHeader } from "@/components/ui/page-header";
import { OrderStatusBadge, STATUS_CONFIG as ORDER_STATUS } from "@/components/status-badge";
import { ErrorState } from "@/components/error-state";
import { formatDate, formatPrice, shortOrderId } from "@/lib/format";
import { cn } from "@/lib/utils";
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
  RotateCcw,
  MapPin,
  Loader2,
  Download,
  Ban,
} from "lucide-react";
import { productImageUrl } from "@/lib/images";

function formatTimestamp(dateStr: string): string {
  try {
    return new Date(dateStr).toLocaleString("en-US", {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  } catch {
    return dateStr;
  }
}

function statusLabel(status: string): string {
  const cfg = ORDER_STATUS[status.toLowerCase()];
  if (cfg) return cfg.label;
  const words = status.replace(/_/g, " ").toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** The seed writes notes like "Order out_for_delivery" that only restate the step's status. */
function isRedundantNote(note: string, status: string): boolean {
  const norm = (v: string) => v.toLowerCase().replace(/_/g, " ").replace(/\s+/g, " ").trim();
  const n = norm(note);
  const st = norm(status);
  return n === st || n === `order ${st}` || n === norm(statusLabel(status)) || n === `order ${norm(statusLabel(status))}`;
}

/** Customer-facing return status; the API sends snake_case states. */
function returnStatusLabel(status: string): string {
  const words = status.replace(/_/g, " ").toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

const REFUND_METHOD_LABEL: Record<string, string> = {
  original_payment: "Original payment",
  store_credit: "Store credit",
};

// ---------------------------------------------------------------------------
// Status timeline
// ---------------------------------------------------------------------------

function StatusTimeline({ history }: { history: StatusHistoryEntry[] }) {
  if (!history || history.length === 0) return null;

  return (
    <ol className="relative">
      {history.map((entry, idx) => {
        const isLatest = idx === history.length - 1;
        const cfg = ORDER_STATUS[entry.status.toLowerCase()];
        const Icon = cfg?.icon ?? Clock;
        const tone = cfg?.tone ?? "neutral";
        const note = entry.notes && !isRedundantNote(entry.notes, entry.status) ? entry.notes : null;

        return (
          <li key={idx} className="relative flex gap-3 pb-5 last:pb-0">
            {!isLatest && (
              <div className="absolute left-[13px] top-8 bottom-1 w-px bg-border" aria-hidden />
            )}
            <div
              className={cn(
                "relative z-10 flex size-7 shrink-0 items-center justify-center rounded-full border",
                isLatest ? TONE_BADGE_CLASS[tone] : "border-border bg-muted text-muted-foreground",
              )}
            >
              <Icon className="size-3.5" aria-hidden />
            </div>
            <div className="min-w-0 flex-1 pt-0.5">
              <div className="flex flex-wrap items-baseline gap-x-2">
                <span className={cn("text-sm font-medium", isLatest ? "text-foreground" : "text-muted-foreground")}>
                  {statusLabel(entry.status)}
                </span>
                <span className="text-xs text-muted-foreground">{formatTimestamp(entry.timestamp)}</span>
              </div>
              {note && <p className="mt-0.5 text-sm text-muted-foreground">{note}</p>}
              {entry.location && (
                <p className="mt-0.5 flex items-center gap-1 text-xs text-muted-foreground">
                  <MapPin className="size-3" aria-hidden />
                  {entry.location}
                </p>
              )}
            </div>
          </li>
        );
      })}
    </ol>
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

  const ret: ReturnInfo | null | undefined = order?.["return"];

  return (
    <PageContainer>
      <Link
        href="/orders"
        className={cn(buttonVariants({ variant: "ghost", size: "sm", className: "-ml-2 mb-4 text-muted-foreground" }))}
      >
        <ArrowLeft data-icon="inline-start" />
        Back to orders
      </Link>

      {loading && <DetailSkeleton />}
      {error && (
        <ErrorState inline message={error} onRetry={loadOrder} homeHref="/orders" homeLabel="Back to orders" />
      )}

      {!loading && !error && order && (
        <>
          <PageHeader
            icon={Package}
            title={
              <span className="flex flex-wrap items-center gap-3">
                Order #{shortOrderId(order.id)}
                <OrderStatusBadge status={order.status} />
              </span>
            }
            description={`Placed on ${formatDate(order.date)}`}
            actions={
              <>
                {(order.status === "placed" || order.status === "confirmed") && (
                  <Dialog open={cancelOpen} onOpenChange={setCancelOpen}>
                    <DialogTrigger
                      render={
                        <Button variant="destructive">
                          <Ban data-icon="inline-start" />
                          Cancel order
                        </Button>
                      }
                    />
                    <DialogContent>
                      <DialogHeader>
                        <DialogTitle>Cancel order</DialogTitle>
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
                          variant="destructive-solid"
                          className="w-full"
                          disabled={cancelling || !cancelReason.trim()}
                          onClick={handleCancelOrder}
                        >
                          {cancelling && <Loader2 className="animate-spin" data-icon="inline-start" />}
                          Confirm cancellation
                        </Button>
                      </div>
                    </DialogContent>
                  </Dialog>
                )}

                {order.status === "delivered" && !ret && (
                  <Dialog open={returnOpen} onOpenChange={setReturnOpen}>
                    <DialogTrigger
                      render={
                        <Button variant="outline">
                          <RotateCcw data-icon="inline-start" />
                          Return order
                        </Button>
                      }
                    />
                    <DialogContent>
                      <DialogHeader>
                        <DialogTitle>Return order</DialogTitle>
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
                              aria-pressed={refundMethod === "original_payment"}
                              onClick={() => setRefundMethod("original_payment")}
                            >
                              Original payment
                            </Button>
                            <Button
                              type="button"
                              variant={refundMethod === "store_credit" ? "default" : "outline"}
                              size="sm"
                              aria-pressed={refundMethod === "store_credit"}
                              onClick={() => setRefundMethod("store_credit")}
                            >
                              Store credit
                            </Button>
                          </div>
                        </div>
                        <Button
                          className="w-full"
                          disabled={returning || !returnReason.trim()}
                          onClick={handleReturnOrder}
                        >
                          {returning && <Loader2 className="animate-spin" data-icon="inline-start" />}
                          Submit return
                        </Button>
                      </div>
                    </DialogContent>
                  </Dialog>
                )}
              </>
            }
          />

          {justPlaced && (
            <div className="mb-6 flex flex-col gap-3 rounded-xl border border-success/30 bg-success/10 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
              <div className={cn("flex items-center gap-3", TONE_TEXT_CLASS.success)}>
                <CheckCircle className="size-5 shrink-0" aria-hidden />
                <div>
                  <p className="font-medium">Order placed</p>
                  <p className="text-sm">We are getting it ready.</p>
                </div>
              </div>
              <Link href="/products" className={cn(buttonVariants({ variant: "outline", size: "sm" }))}>
                Continue shopping
              </Link>
            </div>
          )}

          <div className="grid gap-6 lg:grid-cols-3">
            {/* Left column: timeline, items */}
            <div className="min-w-0 space-y-6 lg:col-span-2">
              {order.status_history && order.status_history.length > 0 && (
                <Card>
                  <CardHeader>
                    <CardTitle>Order status</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <StatusTimeline history={order.status_history} />
                  </CardContent>
                </Card>
              )}

              <Card>
                <CardHeader>
                  <CardTitle>Items ({order.items.length})</CardTitle>
                </CardHeader>
                <CardContent>
                  <Table>
                    <TableHeader>
                      <TableRow className="hover:bg-transparent">
                        <TableHead className="w-14"><span className="sr-only">Image</span></TableHead>
                        <TableHead>Product</TableHead>
                        <TableHead className="text-right">Qty</TableHead>
                        <TableHead className="text-right">Unit price</TableHead>
                        <TableHead className="text-right">Subtotal</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {order.items.map((item, idx) => (
                        <TableRow
                          key={idx}
                          className="cursor-pointer hover:bg-accent"
                          onClick={() => router.push(`/products/${item.product_id}`)}
                        >
                          <TableCell className="w-14">
                            {/* eslint-disable-next-line @next/next/no-img-element */}
                            <img
                              src={productImageUrl(item.product_id, 96, 96, item.image_url, item.category, item.name)}
                              alt=""
                              className="size-10 min-w-10 rounded-md bg-muted object-cover"
                            />
                          </TableCell>
                          <TableCell className="whitespace-normal">
                            <Link
                              href={`/products/${item.product_id}`}
                              className="line-clamp-1 font-medium text-foreground hover:text-primary"
                              onClick={(e) => e.stopPropagation()}
                            >
                              {item.name}
                            </Link>
                            <span className="text-xs text-muted-foreground">{item.category}</span>
                          </TableCell>
                          <TableCell className="text-right tabular-nums">{item.quantity}</TableCell>
                          <TableCell className="text-right tabular-nums text-muted-foreground">
                            {formatPrice(item.unit_price)}
                          </TableCell>
                          <TableCell className="text-right font-medium tabular-nums">
                            {formatPrice(item.subtotal)}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
            </div>

            {/* Right column: summary, shipping, return */}
            <div className="space-y-6">
              <Card>
                <CardHeader>
                  <CardTitle>Order summary</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2">
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-muted-foreground">Subtotal</span>
                    <span className="tabular-nums text-foreground">
                      {formatPrice(order.items?.reduce((sum: number, i: OrderItem) => sum + i.subtotal, 0) ?? 0)}
                    </span>
                  </div>
                  {order.discount > 0 && (
                    <div className="flex items-center justify-between text-sm">
                      <span className="text-muted-foreground">Discount</span>
                      <span className={cn("tabular-nums", TONE_TEXT_CLASS.success)}>
                        -{formatPrice(order.discount)}
                      </span>
                    </div>
                  )}
                  <Separator />
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium text-muted-foreground">Total</span>
                    <span className="text-lg font-semibold tabular-nums text-foreground">
                      {formatPrice(order.total)}
                    </span>
                  </div>
                </CardContent>
              </Card>

              {order.shipping_address && (
                <Card>
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                      <MapPin className="size-4 text-muted-foreground" aria-hidden />
                      Shipping
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <div className="text-sm text-foreground">
                      <p>{order.shipping_address.street}</p>
                      <p>
                        {order.shipping_address.city}, {order.shipping_address.state}{" "}
                        {order.shipping_address.zip}
                      </p>
                    </div>
                    {order.carrier && (
                      <>
                        <Separator />
                        <div className="space-y-1">
                          <div className="flex items-center gap-2 text-sm text-foreground">
                            <Truck className="size-4 text-muted-foreground" aria-hidden />
                            {order.carrier}
                          </div>
                          {order.tracking && (
                            <p className="pl-6 font-mono text-xs text-muted-foreground">{order.tracking}</p>
                          )}
                        </div>
                      </>
                    )}
                    <Separator />
                    <div className="space-y-1">
                      <p className="text-xs font-medium text-muted-foreground">Billing address</p>
                      {order.billing_address &&
                      (order.billing_address.street !== order.shipping_address.street ||
                        order.billing_address.city !== order.shipping_address.city ||
                        order.billing_address.state !== order.shipping_address.state ||
                        order.billing_address.zip !== order.shipping_address.zip) ? (
                        <div className="text-sm text-foreground">
                          {order.billing_address.name && (
                            <p className="font-medium">{order.billing_address.name}</p>
                          )}
                          <p>{order.billing_address.street}</p>
                          <p>
                            {order.billing_address.city}, {order.billing_address.state}{" "}
                            {order.billing_address.zip}
                          </p>
                          {order.billing_address.country && <p>{order.billing_address.country}</p>}
                        </div>
                      ) : (
                        <p className="text-sm text-muted-foreground">Same as shipping address</p>
                      )}
                    </div>
                  </CardContent>
                </Card>
              )}

              {ret && (
                <Card>
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                      <RotateCcw className="size-4 text-muted-foreground" aria-hidden />
                      Return
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <dl className="space-y-2 text-sm">
                      <div className="flex items-center justify-between gap-3">
                        <dt className="text-muted-foreground">Status</dt>
                        <dd>
                          <StatusBadge label={returnStatusLabel(ret.status)} tone="warning" />
                        </dd>
                      </div>
                      <div className="flex items-start justify-between gap-3">
                        <dt className="shrink-0 text-muted-foreground">Reason</dt>
                        <dd className="text-right text-foreground">{ret.reason}</dd>
                      </div>
                      <div className="flex items-center justify-between gap-3">
                        <dt className="text-muted-foreground">Refund method</dt>
                        <dd className="text-foreground">
                          {(ret.refund_method && REFUND_METHOD_LABEL[ret.refund_method]) ?? ret.refund_method ?? "Not set"}
                        </dd>
                      </div>
                      {ret.refund_amount != null && (
                        <div className="flex items-center justify-between gap-3">
                          <dt className="text-muted-foreground">Refund amount</dt>
                          <dd className={cn("font-medium tabular-nums", TONE_TEXT_CLASS.success)}>
                            {formatPrice(ret.refund_amount)}
                          </dd>
                        </div>
                      )}
                    </dl>
                    {(ret.created_at || ret.resolved_at) && (
                      <>
                        <Separator />
                        <div className="space-y-1.5 text-xs text-muted-foreground">
                          {ret.created_at && (
                            <div className="flex items-center justify-between">
                              <span>Return started</span>
                              <span>{formatDate(ret.created_at)}</span>
                            </div>
                          )}
                          {ret.resolved_at && (
                            <div className="flex items-center justify-between">
                              <span>Refund processed</span>
                              <span>{formatDate(ret.resolved_at)}</span>
                            </div>
                          )}
                        </div>
                      </>
                    )}
                    {ret.return_label_url && (
                      <>
                        <Separator />
                        <div className="space-y-2">
                          <Button
                            variant="outline"
                            className="w-full"
                            onClick={() => window.open(apiUrl(ret.return_label_url || ""), "_blank")}
                          >
                            <Download data-icon="inline-start" />
                            Download return label
                          </Button>
                          <p className="text-center text-xs text-muted-foreground">
                            Print the label and drop the parcel off at any carrier location.
                          </p>
                        </div>
                      </>
                    )}
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
