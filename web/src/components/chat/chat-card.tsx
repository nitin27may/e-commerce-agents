"use client";

import Link from "next/link";
import type { LucideIcon } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * The frame every generative-UI card in the chat shares: one width, one header,
 * one body rhythm, one footer. A card only decides what goes inside.
 */
export interface ChatCardProps {
  icon?: LucideIcon;
  title?: React.ReactNode;
  /** Right side of the header: status badges, a date. */
  meta?: React.ReactNode;
  /** Actions row under the body. */
  footer?: React.ReactNode;
  /** Removes the body padding, for content that runs edge to edge (item tables). */
  flush?: boolean;
  /** Names the card for assistive tech when the title is not plain text. */
  label?: string;
  className?: string;
  children?: React.ReactNode;
}

export function ChatCard({ icon: Icon, title, meta, footer, flush, label, className, children }: ChatCardProps) {
  return (
    <section
      aria-label={label ?? (typeof title === "string" ? title : undefined)}
      className={cn("w-full max-w-lg overflow-hidden rounded-xl border border-border bg-card text-card-foreground shadow-sm", className)}
    >
      {(title || meta) && (
        <header className="flex min-h-11 items-center gap-2 border-b border-border bg-muted/60 px-4 py-2.5 text-sm font-medium">
          {Icon && <Icon className="size-4 shrink-0 text-muted-foreground" aria-hidden />}
          <div className="min-w-0 flex-1 truncate text-foreground">{title}</div>
          {meta && <div className="flex shrink-0 items-center gap-1.5">{meta}</div>}
        </header>
      )}
      {children != null && children !== false && <div className={flush ? undefined : "space-y-3 p-4"}>{children}</div>}
      {footer && <footer className="flex flex-wrap items-center gap-2 border-t border-border px-4 py-2">{footer}</footer>}
    </section>
  );
}

/** A small caption over a section of a card body ("By warehouse", "Active coupons"). */
export function CardSection({ title, icon: Icon, children }: { title: string; icon?: LucideIcon; children: React.ReactNode }) {
  return (
    <div>
      <p className="mb-1.5 flex items-center gap-1 text-xs font-medium text-muted-foreground">
        {Icon && <Icon className="size-3" aria-hidden />}
        {title}
      </p>
      {children}
    </div>
  );
}

type CardActionVariant = "primary" | "outline" | "ghost" | "destructive";

const VARIANT: Record<CardActionVariant, "default" | "outline" | "ghost" | "destructive"> = {
  primary: "default",
  outline: "outline",
  ghost: "ghost",
  destructive: "destructive",
};

export interface CardActionProps {
  children?: React.ReactNode;
  icon?: LucideIcon;
  variant?: CardActionVariant;
  /** Renders a link styled as the button. */
  href?: string;
  /** Opens `href` in a new tab, so the conversation stays where it is. */
  external?: boolean;
  /** Icon only; `label` becomes the accessible name and tooltip. */
  iconOnly?: boolean;
  label?: string;
  onClick?: (e: React.MouseEvent) => void;
  disabled?: boolean;
  pressed?: boolean;
  className?: string;
}

/** The one button size cards use, so every card's actions line up the same way. */
export function CardAction({
  children,
  icon: Icon,
  variant = "outline",
  href,
  external,
  iconOnly,
  label,
  onClick,
  disabled,
  pressed,
  className,
}: CardActionProps) {
  const classes = cn(
    buttonVariants({ variant: VARIANT[variant], size: iconOnly ? "icon-sm" : "sm" }),
    !iconOnly && "h-7 text-xs",
    className,
  );
  const content = (
    <>
      {Icon && <Icon className="size-3.5" aria-hidden />}
      {!iconOnly && children}
    </>
  );

  if (href) {
    return (
      <Link
        href={href}
        className={classes}
        aria-label={iconOnly ? label : undefined}
        title={iconOnly ? label : undefined}
        {...(external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
      >
        {content}
      </Link>
    );
  }

  return (
    <button
      type="button"
      className={classes}
      onClick={onClick}
      disabled={disabled}
      aria-pressed={pressed}
      aria-label={iconOnly ? label : undefined}
      title={iconOnly ? label : undefined}
    >
      {content}
    </button>
  );
}
