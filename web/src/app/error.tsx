"use client";

import { ErrorState } from "@/components/error-state";

export default function Error({
  error,
  unstable_retry,
}: {
  error: Error & { digest?: string };
  unstable_retry: () => void;
}) {
  return <ErrorState error={error} digest={error.digest} onRetry={unstable_retry} fullScreen />;
}
