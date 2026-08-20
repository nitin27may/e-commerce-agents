import { type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

export interface StatDelta {
  /** Pre-formatted delta label, e.g. "+3 this week" or "-12%". */
  value: string;
  trend: "up" | "down" | "neutral";
}

export interface StatCardProps {
  label: string;
  value: string | number;
  icon?: LucideIcon;
  delta?: StatDelta;
  /** Secondary line under the value. */
  hint?: string;
  className?: string;
}

const TREND_CLASS: Record<StatDelta["trend"], string> = {
  up: "text-success",
  down: "text-destructive",
  neutral: "text-muted-foreground",
};

/** KPI tile: label + large value + optional icon, delta, and hint. */
export function StatCard({
  label,
  value,
  icon: Icon,
  delta,
  hint,
  className,
}: StatCardProps) {
  return (
    <div
      data-slot="stat-card"
      className={cn(
        "rounded-xl bg-card p-4 text-card-foreground ring-1 ring-foreground/10",
        className,
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
          {label}
        </p>
        {Icon && (
          <Icon className="size-4 shrink-0 text-muted-foreground" aria-hidden />
        )}
      </div>
      <div className="mt-2 flex items-baseline gap-2">
        <span className="text-2xl font-semibold tracking-tight tabular-nums">
          {value}
        </span>
        {delta && (
          <span className={cn("text-xs font-medium", TREND_CLASS[delta.trend])}>
            {delta.value}
          </span>
        )}
      </div>
      {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}
