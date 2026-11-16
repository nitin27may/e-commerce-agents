"use client";

import { Inter } from "next/font/google";
import { ErrorState } from "@/components/error-state";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-sans",
});

// Replaces the root layout when it throws, so it must supply its own
// <html>/<body>, global styles and font. There is no AuthProvider or Toaster
// here, and ErrorState needs neither.
export default function GlobalError({
  error,
  unstable_retry,
}: {
  error: Error & { digest?: string };
  unstable_retry: () => void;
}) {
  return (
    <html lang="en" className={`${inter.variable} h-full antialiased`}>
      <body className="min-h-full bg-background font-sans text-foreground">
        <title>Something went wrong</title>
        <ErrorState error={error} digest={error.digest} onRetry={unstable_retry} fullScreen />
      </body>
    </html>
  );
}
