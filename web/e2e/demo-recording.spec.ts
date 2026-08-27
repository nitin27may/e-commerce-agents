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
  // Located by aria-label, not by button text. The trigger displays the CURRENT
  // mode's label, so any text-based locator only works before the first switch
  // and silently stops matching afterwards — which is exactly how the second
  // switch below failed for five recordings while the spec exited 0 each time.
  const modeSwitcher = page.getByLabel("Orchestration mode");
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
  //
  // Switch to workflow:return-replace FIRST. The approval gate lives in that
  // workflow's graph, not in the platform — asking a return question while
  // still in pre-purchase mode routes it through a workflow with no gate, which
  // is why the first two recordings logged "no pending approval on /runs" and
  // passed anyway, losing the clip's last two beats.
  const switcher2 = page.getByLabel("Orchestration mode");
  if (await switcher2.isVisible().catch(() => false)) {
    await switcher2.click();
    await beat(page, 1_200);
    // The label is "Return & Replace (sequential + in-workflow HITL)". A `.?`
    // between the words cannot span " & ", so the previous pattern matched
    // nothing, the `else` branch pressed Escape, and the clip asked its return
    // question in pre-purchase mode — a workflow with no approval gate. That is
    // the actual reason five recordings logged "no pending approval on /runs"
    // and still exited 0.
    const rr = page.getByRole("option", { name: /return\s*&\s*replace/i }).first();
    if (await rr.isVisible().catch(() => false)) {
      await rr.click();
    } else {
      await page.keyboard.press("Escape");
    }
    await beat(page, 1_200);
  }

  // The order is looked up, not hardcoded, and that is not fussiness.
  //
  // FOUR constraints must hold at once for the approval gate to fire, and each
  // one cost a recording to discover:
  //
  //   workflow:return-replace selected  the gate lives in that graph, not the
  //                                     platform — a return asked in any other
  //                                     mode routes through a workflow with no gate
  //   status 'delivered'                the mode falls back to the user's MOST
  //                                     RECENT order, which is 'shipped'
  //   within the 30-day return window   the two largest delivered orders are
  //                                     39 and 42 days old
  //   total above $500                  RETURN_HITL_THRESHOLD; a cheaper return
  //                                     is auto-approved and never pauses
  //
  // And a fifth, which is why hardcoding failed even after the other four were
  // right: a return can only be initiated ONCE per order. The first recording
  // consumes it, and every rerun afterwards takes a different path and silently
  // loses the beat. Alice has exactly one qualifying order at any moment, so a
  // hardcoded id works precisely once.
  //
  // Reading /api/orders and filtering makes the spec re-runnable, and it fails
  // with a real explanation when the seed data cannot support the take.
  // Absolute URL: the frontend is served from :3000 and the orchestrator lives
  // on :8080, so a same-origin "/api/orders" reaches Next.js and comes back as
  // HTML. Mirrors NEXT_PUBLIC_API_URL's default, which is what the app itself
  // uses.
  const apiBase = process.env.E2E_API_URL ?? "http://localhost:8080";

  const orderId = await page.evaluate(async (base) => {
    const token = localStorage.getItem("ecommerce_access_token");
    const res = await fetch(`${base}/api/orders?limit=50`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    const body = await res.json();
    const orders = Array.isArray(body) ? body : (body.orders ?? body.entries ?? []);
    // The orders endpoint returns `date`, not `created_at` — the shape differs
    // from the DB column, which is easy to assume and wrong.
    const cutoff = Date.now() - 25 * 24 * 60 * 60 * 1000; // inside the 30-day window, with margin
    const match = orders.find(
      (o: Record<string, unknown>) =>
        o.status === "delivered" &&
        Number(o.total) > 500 &&
        new Date(String(o.date)).getTime() > cutoff
    );
    return match ? String((match as Record<string, unknown>).id) : "";
  }, apiBase);

  expect(
    orderId,
    "no delivered order over $500 inside the 30-day window — the seed data cannot " +
      "produce an approval pause, so re-seed (./scripts/dev.sh --clean) before recording"
  ).not.toBe("");

  await typeAndSend(
    page,
    `I want to return order ${orderId} — it is not what I expected.`
  );
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
    // Fail, do not log.
    //
    // This branch used to console.log and let the test pass, which meant six
    // consecutive recordings produced a clip missing its last two beats — the
    // approval and the resume — while the spec exited 0 every time. A recording
    // script that reports success for an incomplete take is the same failure
    // this repo keeps finding elsewhere: healthy-looking, quietly wrong.
    //
    // Everything needed to diagnose it goes in the message, because the clip is
    // the artifact and nobody re-watches a green run.
    const modeLabel = await page.getByLabel("Orchestration mode").textContent().catch(() => "(not found)");
    const bodyText = (await page.locator("main").textContent().catch(() => "")) ?? "";
    throw new Error(
      "No pending approval on /runs, so the clip is missing its approval and resume beats.\n" +
        `Composer mode at the end of the run: ${modeLabel}\n` +
        "The return must satisfy ALL FOUR of: workflow:return-replace selected, order " +
        "status 'delivered', within the 30-day return window, and total above " +
        "RETURN_HITL_THRESHOLD ($500).\n" +
        `/runs page text (first 400 chars): ${bodyText.slice(0, 400)}`
    );
  }

  await beat(page, 2_000);

  // The one real assertion: we ended somewhere sensible, so a broken take fails
  // loudly instead of silently producing an unusable video.
  expect(page.url()).toMatch(/\/(runs|chat)/);
});
