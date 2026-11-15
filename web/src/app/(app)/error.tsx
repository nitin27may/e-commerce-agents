"use client";

import { ErrorState } from "@/components/error-state";

// Sits inside the authenticated shell, so the sidebar and top bar stay usable.
export default function AppError({
  error,
  unstable_retry,
}: {
  error: Error & { digest?: string };
  unstable_retry: () => void;
}) {
  return (
    <ErrorState
      error={error}
      digest={error.digest}
      onRetry={unstable_retry}
      homeHref="/home"
      homeLabel="Back to dashboard"
    />
  );
}
