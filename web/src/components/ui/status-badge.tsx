import { type LucideIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/**
 * The five states any generative-UI status/health signal maps onto — driven
 * entirely by the success/warning/info/destructive design tokens (Phase 8.4
 * Stage 1), never a literal color. `neutral` is the catch-all for anything
 * that isn't a positive/negative/cautionary/informational signal.
 */
export type SemanticTone = "success" | "warning" | "info" | "destructive" | "neutral";

/** Badge background/text/border classes, keyed by tone. */
export const TONE_BADGE_CLASS: Record<SemanticTone, string> = {
  success: "bg-success/10 text-success border-success/30 dark:bg-success/15",
  warning: "bg-warning/10 text-warning border-warning/30 dark:bg-warning/15",
  info: "bg-info/10 text-info border-info/30 dark:bg-info/15",
  destructive: "bg-destructive/10 text-destructive border-destructive/30 dark:bg-destructive/15",
  neutral: "bg-muted/50 text-muted-foreground border-border",
};

/** Plain text-color class, keyed by tone — for icons/values with no badge chrome. */
export const TONE_TEXT_CLASS: Record<SemanticTone, string> = {
  success: "text-success",
  warning: "text-warning",
  info: "text-info",
  destructive: "text-destructive",
  neutral: "text-muted-foreground",
};

export interface StatusBadgeProps {
  label: string;
  tone: SemanticTone;
  icon?: LucideIcon;
  className?: string;
}

/**
 * Generic status/health badge for any boolean or enum value a specialist
 * emits (in_stock, risk_level, sentiment, ...) — driven by a tone lookup
 * instead of a hardcoded per-domain color switch, so every domain-specific
 * usage (order status, stock level, sentiment risk) shares one mapping.
 */
export function StatusBadge({ label, tone, icon: Icon, className }: StatusBadgeProps) {
  return (
    <Badge variant="outline" className={cn(TONE_BADGE_CLASS[tone], className)}>
      {Icon && <Icon className="mr-1 size-3" />}
      {label}
    </Badge>
  );
}
