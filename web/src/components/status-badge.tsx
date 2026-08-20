import { Badge } from "@/components/ui/badge";
import { Package, Truck, CheckCircle, XCircle, RotateCcw, Clock, ShoppingCart } from "lucide-react";

const STATUS_CONFIG: Record<string, { label: string; color: string; icon: React.ElementType }> = {
  placed: { label: "Placed", color: "bg-info/10 text-info border-info/30 dark:bg-info/15", icon: ShoppingCart },
  confirmed: { label: "Confirmed", color: "bg-info/10 text-info border-info/30 dark:bg-info/15", icon: Package },
  shipped: { label: "Shipped", color: "bg-warning/10 text-warning border-warning/30 dark:bg-warning/15", icon: Truck },
  out_for_delivery: { label: "Out for Delivery", color: "bg-warning/10 text-warning border-warning/30 dark:bg-warning/15", icon: Truck },
  delivered: { label: "Delivered", color: "bg-success/10 text-success border-success/30 dark:bg-success/15", icon: CheckCircle },
  returned: { label: "Returned", color: "bg-warning/10 text-warning border-warning/30 dark:bg-warning/15", icon: RotateCcw },
  cancelled: { label: "Cancelled", color: "bg-destructive/10 text-destructive border-destructive/30 dark:bg-destructive/15", icon: XCircle },
};

export function OrderStatusBadge({ status }: { status: string }) {
  const cfg = STATUS_CONFIG[status] || { label: status, color: "bg-muted/50 text-muted-foreground border-border", icon: Clock };
  const Icon = cfg.icon;
  return (
    <Badge variant="outline" className={cfg.color}>
      <Icon className="mr-1 size-3" />
      {cfg.label}
    </Badge>
  );
}

export { STATUS_CONFIG };
