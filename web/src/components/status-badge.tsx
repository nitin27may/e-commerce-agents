import { StatusBadge, type SemanticTone } from "@/components/ui/status-badge";
import { type LucideIcon, Package, Truck, CheckCircle, XCircle, RotateCcw, Clock, ShoppingCart } from "lucide-react";

// Order-domain status -> the generic StatusBadge primitive (Phase 8.4 Stage
// 3) — this file now only owns the order-specific label/icon/tone mapping,
// not the tone->color logic itself, which lives once in ui/status-badge.tsx.
const STATUS_CONFIG: Record<string, { label: string; tone: SemanticTone; icon: LucideIcon }> = {
  placed: { label: "Placed", tone: "info", icon: ShoppingCart },
  confirmed: { label: "Confirmed", tone: "info", icon: Package },
  shipped: { label: "Shipped", tone: "warning", icon: Truck },
  out_for_delivery: { label: "Out for Delivery", tone: "warning", icon: Truck },
  delivered: { label: "Delivered", tone: "success", icon: CheckCircle },
  returned: { label: "Returned", tone: "warning", icon: RotateCcw },
  cancelled: { label: "Cancelled", tone: "destructive", icon: XCircle },
};

export function OrderStatusBadge({ status }: { status: string }) {
  const cfg = STATUS_CONFIG[status] || { label: status, tone: "neutral" as const, icon: Clock };
  return <StatusBadge label={cfg.label} tone={cfg.tone} icon={cfg.icon} />;
}

export { STATUS_CONFIG };
