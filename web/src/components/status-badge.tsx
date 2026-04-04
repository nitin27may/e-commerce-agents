import { Badge } from "@/components/ui/badge";
import { Package, Truck, CheckCircle, XCircle, RotateCcw, Clock, ShoppingCart } from "lucide-react";

const STATUS_CONFIG: Record<string, { label: string; color: string; icon: React.ElementType }> = {
  placed: { label: "Placed", color: "bg-blue-50 text-blue-700 border-blue-200", icon: ShoppingCart },
  confirmed: { label: "Confirmed", color: "bg-indigo-50 text-indigo-700 border-indigo-200", icon: Package },
  shipped: { label: "Shipped", color: "bg-amber-50 text-amber-700 border-amber-200", icon: Truck },
  out_for_delivery: { label: "Out for Delivery", color: "bg-amber-50 text-amber-600 border-amber-200", icon: Truck },
  delivered: { label: "Delivered", color: "bg-green-50 text-green-700 border-green-200", icon: CheckCircle },
  returned: { label: "Returned", color: "bg-orange-50 text-orange-700 border-orange-200", icon: RotateCcw },
  cancelled: { label: "Cancelled", color: "bg-red-50 text-red-700 border-red-200", icon: XCircle },
};

export function OrderStatusBadge({ status }: { status: string }) {
  const cfg = STATUS_CONFIG[status] || { label: status, color: "bg-slate-50 text-slate-700 border-slate-200", icon: Clock };
  const Icon = cfg.icon;
  return (
    <Badge variant="outline" className={cfg.color}>
      <Icon className="mr-1 size-3" />
      {cfg.label}
    </Badge>
  );
}

export { STATUS_CONFIG };
