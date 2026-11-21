"use client";

import { useEffect } from "react";

/**
 * Run an async loader once `enabled` is true, and again whenever `load` changes
 * (pages memoise it on the filters it reads).
 *
 * `load` owns its own loading/error state, the way the page loaders always
 * have. Keeping the effect here, with the loader passed in, means a component's
 * effect body no longer reads as a synchronous setState, which is what
 * `react-hooks/set-state-in-effect` is there to catch: the loader starts a
 * request and sets state as the response arrives.
 */
export function useLoadEffect(load: () => unknown, enabled: boolean): void {
  useEffect(() => {
    if (enabled) void load();
  }, [enabled, load]);
}
