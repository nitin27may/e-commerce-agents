import { cn } from "@/lib/utils";

export interface SectionHeaderProps {
  title: string;
  description?: string;
  /** Small uppercase label above the title. */
  eyebrow?: string;
  /** Right-aligned slot, e.g. a button or filter. */
  action?: React.ReactNode;
  className?: string;
  /** Heading element. Defaults to h2; a page with no other h1 passes "h1". Size is the same either way. */
  as?: "h1" | "h2";
}

/** Consistent header for page/section blocks across the app shell. */
export function SectionHeader({
  title,
  description,
  eyebrow,
  action,
  className,
  as: Heading = "h2",
}: SectionHeaderProps) {
  return (
    <div className={cn("mb-4 flex items-start justify-between gap-4", className)}>
      <div className="space-y-1">
        {eyebrow && (
          <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
            {eyebrow}
          </p>
        )}
        <Heading className="font-heading text-lg font-semibold tracking-tight">
          {title}
        </Heading>
        {description && (
          <p className="text-sm text-muted-foreground">{description}</p>
        )}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}
