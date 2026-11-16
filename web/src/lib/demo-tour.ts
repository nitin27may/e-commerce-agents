"use client";

import { useEffect, useState } from "react";
import { api, type DemoTour, type DemoTourChip } from "@/lib/api";

export type { DemoTour, DemoTourChip };

/**
 * One manifest fetch per page load, shared by the chips, the miss callout and
 * the reset control. `null` means "not a demo" (the backend answered 404, or
 * the fetch failed) and every consumer renders nothing for it.
 */
let cached: Promise<DemoTour | null> | null = null;

export function loadDemoTour(): Promise<DemoTour | null> {
  if (!cached) cached = api.getDemoTour();
  return cached;
}

/** Forget the cached manifest (tests, and after a sign-in change). */
export function resetDemoTourCache(): void {
  cached = null;
}

/** The manifest once loaded; `null` while loading and for a backend that is not a demo. */
export function useDemoTour(): DemoTour | null {
  const [tour, setTour] = useState<DemoTour | null>(null);
  useEffect(() => {
    let cancelled = false;
    loadDemoTour().then((t) => {
      if (!cancelled) setTour(t);
    });
    return () => {
      cancelled = true;
    };
  }, []);
  return tour;
}
