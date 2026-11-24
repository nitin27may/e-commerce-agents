import { test } from "@playwright/test";

/**
 * The key-free run (`./scripts/dev.sh --no-key`, then `E2E_NO_KEY=1 pnpm exec playwright test`).
 *
 * The no-key stack answers only the prompts recorded in
 * `agents/python/demo/fixtures/manifest.json`. A test that sends a prompt nobody recorded gets a
 * replay miss, not an answer, so its assertions on the answer cannot hold. Those tests call
 * `skipWithoutKey(reason)` first, and the key-free run reports them as skipped with the reason.
 * With a model behind the stack (the default) it does nothing.
 */
export const NO_KEY = process.env.E2E_NO_KEY === "1";

export function skipWithoutKey(reason: string) {
  test.skip(NO_KEY, `no-key run: ${reason}`);
}
