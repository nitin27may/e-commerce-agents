import { type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * The one content column for every page under app/(app) except Chat and Lab,
 * which stay full-bleed. Mobile keeps a narrower gutter so cards are not
 * squeezed on a 412px screen.
 */
export function PageContainer({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div
      data-slot="page-container"
      className={cn("mx-auto w-full max-w-6xl px-4 py-6 sm:px-8", className)}
    >
      {children}
    </div>
  );
}

export interface PageHeaderProps {
  title: React.ReactNode;
  description?: React.ReactNode;
  icon?: LucideIcon;
  /** Right-aligned slot for page-level buttons. Wraps under the title on narrow screens. */
  actions?: React.ReactNode;
  className?: string;
}

/** Page title block shared by every app page, so headings line up across routes. */
export function PageHeader({ title, description, icon: Icon, actions, className }: PageHeaderProps) {
  return (
    <header
      data-slot="page-header"
      className={cn("mb-6 flex flex-wrap items-start justify-between gap-x-6 gap-y-3", className)}
    >
      <div className="flex min-w-0 items-start gap-3">
        {Icon && (
          <div
            data-slot="page-header-icon"
            className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary"
          >
            <Icon className="size-5" aria-hidden />
          </div>
        )}
        <div className="min-w-0 space-y-1">
          <h1 className="font-heading text-2xl font-semibold tracking-tight text-foreground">
            {title}
          </h1>
          {description && <p className="text-sm text-muted-foreground">{description}</p>}
        </div>
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}
