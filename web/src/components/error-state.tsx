"use client";

import Link from "next/link";
import { useEffect } from "react";
import { AlertTriangle, FileQuestion, Home, RotateCcw } from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";

type ErrorStateProps = {
  /** "error" shows a retry action; "not-found" only offers the way home. */
  kind?: "error" | "not-found";
  homeHref?: string;
  homeLabel?: string;
  /** Re-attempt the failed render (Next's `unstable_retry`). Omit for not-found. */
  onRetry?: () => void;
  /** Opaque server-log correlation id. The message itself is never rendered. */
  digest?: string;
  /** Logged to the console only — never shown, as server errors can leak detail. */
  error?: Error;
  /** Fill the viewport (root boundaries) instead of sitting inside a shell. */
  fullScreen?: boolean;
};

/**
 * Shared body for every error boundary and the 404 page. Deliberately renders
 * fixed copy: in production Next already redacts server errors, but in dev the
 * message is forwarded verbatim, and an agent backend can put anything in one.
 */
export function ErrorState({
  kind = "error",
  homeHref = "/",
  homeLabel = "Back to home",
  onRetry,
  digest,
  error,
  fullScreen = false,
}: ErrorStateProps) {
  useEffect(() => {
    if (error) console.error(error);
  }, [error]);

  const notFound = kind === "not-found";
  const Icon = notFound ? FileQuestion : AlertTriangle;

  return (
    <div
      role={notFound ? undefined : "alert"}
      className={`flex flex-col items-center justify-center px-4 text-center ${
        fullScreen ? "min-h-screen" : "py-24"
      }`}
    >
      <div className="mb-4 rounded-full bg-muted p-4">
        <Icon className={`size-8 ${notFound ? "text-muted-foreground" : "text-destructive"}`} />
      </div>
      <h1 className="text-xl font-semibold text-foreground">
        {notFound ? "Page not found" : "Something went wrong"}
      </h1>
      <p className="mt-2 max-w-sm text-sm text-muted-foreground">
        {notFound
          ? "The page you are looking for does not exist or has moved."
          : "This page couldn't load. You can try again, or head back to the start."}
      </p>
      {digest && (
        <p className="mt-2 font-mono text-xs text-muted-foreground">Reference: {digest}</p>
      )}
      <div className="mt-6 flex flex-wrap items-center justify-center gap-2">
        {onRetry && (
          <Button onClick={onRetry}>
            <RotateCcw data-icon="inline-start" />
            Try again
          </Button>
        )}
        {/* A real <a>: base-ui's Button would stamp role="button" on a rendered link. */}
        <Link href={homeHref} className={buttonVariants({ variant: "outline" })}>
          <Home data-icon="inline-start" />
          {homeLabel}
        </Link>
      </div>
    </div>
  );
}
