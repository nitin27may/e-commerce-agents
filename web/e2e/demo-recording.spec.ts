/**
 * demo-recording.spec.ts
 *
 * Records the 60-90 second silent clip that sits at the top of the README and
 * the docs site home. Not a test — it asserts almost nothing. It drives the app
 * through the six things that make this repo different, in one continuous take:
 *
 *   1. Streaming chat with generative UI (product cards, not raw JSON)
 *   2. Switching orchestration mode from the composer
 *   3. The orchestration graph animating node-by-node from live SSE events
 *   4. A workflow pausing on its human-in-the-loop gate
 *   5. The pending approval on /runs
 *   6. Approve, and the run resuming from a real Postgres checkpoint
 *
 * Requires the full stack running with a live LLM key:
 *   ./scripts/dev.sh --demo
 *
 * Run:
 *   cd web && pnpm exec playwright test e2e/demo-recording.spec.ts
 *
 * Output: test-results/<...>/video.webm. Convert for embedding with
 *   ffmpeg -i video.webm -c:v libx264 -crf 24 -pix_fmt yuv420p demo.mp4
 *
 * Being a committed script rather than a manual screen capture means it can be
 * re-recorded after any UI change instead of decaying into a stale clip.
 *
 * ── Two things that will ruin the take, both learned the hard way ────────────
 *
 * Prompts must be ones the catalogue can actually answer. `search_products`
 * does ILIKE '%<whole phrase>%', so "running shoes" matches nothing while
 * "Allbirds" matches — the seeded catalogue has no product whose text contains
 * the word "shoes". A natural-sounding prompt that returns "I couldn't find
 * any" is the worst possible first impression, so every prompt below is
 * verified against the seed data.
 *
 * Never wait on text like "Routing to specialists...". The composer's submit
 * button swaps to a stop icon while a turn is in flight and swaps back when it
 * completes; that is the only reliable signal. Waiting on text races the
 * stream, and a click landing mid-stream silently no-ops.
 */

import { test, expect, type Page } from "@playwright/test";

const CUSTOMER = { email: "alice.johnson@gmail.com", password: "customer123" };

// Recording only — no retries, one worker, generous budget for real LLM calls.
test.use({
  viewport: { width: 1440, height: 900 },
  video: { mode: "on", size: { width: 1440, height: 900 } },
});

test.setTimeout(600_000);

/** Human-paced pause. The clip is watched, not benchmarked — dead-fast UI
 *  transitions read as glitches on video. */
const beat = (page: Page, ms = 1_200) => page.waitForTimeout(ms);

async function login(page: Page) {
  await page.goto("/login");
  await page.evaluate(() => {
    localStorage.removeItem("ecommerce_user");
    localStorage.removeItem("ecommerce_access_token");
    localStorage.removeItem("ecommerce_refresh_token");
  });
  await page.goto("/login");
  await page.fill('input[type="email"]', CUSTOMER.email);
  await page.fill('input[type="password"]', CUSTOMER.password);
  await page.getByRole("button", { name: /log\s*in|sign\s*in/i }).click();
  await page.waitForURL(/\/chat/, { timeout: 20_000 });
  await page.waitForLoadState("networkidle").catch(() => {});
}

/** Type at human speed so the video shows typing rather than text teleporting in. */
async function typeAndSend(page: Page, message: string) {
  const input = page.locator("textarea").first();
  await input.waitFor({ state: "visible", timeout: 15_000 });
  await input.click();
  await input.pressSequentially(message, { delay: 28 });
  await beat(page, 500);
  await input.press("Enter");
}

/** Wait for the turn to actually finish — see the header note. */
async function waitForTurn(page: Page) {
  const STOP = 'button[aria-label="Stop"], button:has(svg.lucide-square)';
  await page.waitForSelector(STOP, { timeout: 30_000, state: "attached" }).catch(() => {});
  await page.waitForSelector(STOP, { timeout: 180_000, state: "detached" });
  await beat(page, 1_500);
}

test("demo clip — chat, modes, graph, approval, resume", async ({ page }) => {
  await login(page);
  await beat(page, 1_500);

  // ── 1. Streaming chat + generative UI ────────────────────────────────────
  // "Allbirds" is a literal substring of a seeded product name, so ILIKE finds
  // it and the answer renders as cards.
  await typeAndSend(page, "What Allbirds products do you have?");
  await waitForTurn(page);
  await beat(page, 2_000);

  // ── 2. A follow-up, to show conversation context surviving ───────────────
  await typeAndSend(page, "How much are they?");
  await waitForTurn(page);
  await beat(page, 2_000);

  // ── 3. Switch orchestration mode, then re-ask ────────────────────────────
  // The mode switcher is fed by GET /api/orchestration/modes. Opening it on
  // camera is the point: the same question, routed a different way.
  const modeSwitcher = page
    .locator('[data-testid="mode-switcher"], button:has-text("tool"), button:has-text("Tool")')
    .first();
  if (await modeSwitcher.isVisible().catch(() => false)) {
    await modeSwitcher.click();
    await beat(page, 1_200);
    const preP = page.getByRole("option", { name: /pre-purchase/i }).first();
    if (await preP.isVisible().catch(() => false)) {
      await preP.click();
    } else {
      await page.keyboard.press("Escape");
    }
    await beat(page, 1_200);
  }

  await typeAndSend(page, "I'm considering the Allbirds Wool Runners — should I buy them?");
  await waitForTurn(page);
  await beat(page, 3_000); // hold on the animated graph

  // ── 4. Trigger the HITL gate ─────────────────────────────────────────────
  await typeAndSend(page, "I want to return my most recent order, it does not fit.");
  await waitForTurn(page);
  await beat(page, 2_500);

  // ── 5-6. The pending approval on /runs, then resume ──────────────────────
  await page.goto("/runs");
  await page.waitForLoadState("networkidle").catch(() => {});
  await beat(page, 2_500);

  const approve = page.getByRole("button", { name: /approve/i }).first();
  if (await approve.isVisible().catch(() => false)) {
    await approve.scrollIntoViewIfNeeded();
    await beat(page, 1_200);
    await approve.click();
    await beat(page, 4_000); // the run resumes from its checkpoint
  } else {
    // Not a failure of the recording — the return may not have crossed
    // RETURN_HITL_THRESHOLD. Log it so the operator knows why the clip is short.
    console.log("[demo] no pending approval on /runs — the return did not cross the HITL threshold");
  }

  await beat(page, 2_000);

  // The one real assertion: we ended somewhere sensible, so a broken take fails
  // loudly instead of silently producing an unusable video.
  expect(page.url()).toMatch(/\/(runs|chat)/);
});
