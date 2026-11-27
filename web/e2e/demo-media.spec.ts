/**
 * demo-media.spec.ts
 *
 * The documentation clips, one test per scenario, recorded on the NO-KEY stack: every model answer is a recorded
 * replay fixture (agents/python/demo/fixtures/manifest.json) or a workflow mode that calls no model.
 *
 *   ./scripts/dev.sh --no-key            # the stack, on a freshly seeded database
 *   scripts/record-media.sh              # every scenario -> docs/images/<name>.gif + docs/media/<name>.mp4
 *   scripts/record-media.sh return lab   # just these (the name is the test title before the colon)
 *
 * Skipped unless RECORD_MEDIA=1. Nothing here asserts product behaviour; a missing beat fails the take loudly
 * instead of producing a clip that quietly lacks it. A test that records several takes names them <clip>-1, <clip>-2;
 * scripts/record-media.sh joins them into one clip.
 *
 * Shopper clips run on /chat, which shows answers and cards only. The "lab" clip runs on the staff Lab (/lab), the
 * same chat with its machinery shown. Every clip: 1280 x 800 at device scale 2, light theme, the pointer visible.
 * Capture and encoding settings, and why: scripts/demo/README.md.
 *
 * Order matters, because two clips change data that recorded answers read. "return" approves a return on Alice's
 * order. "hero" places an order, which takes a Sony WH-1000XM5 out of stock, and the "multi" and "lab" answers
 * read that stock count. So "return" runs second to last and "hero" last. Re-run on a freshly seeded database.
 */

import { test, expect, type Browser, type Page } from "@playwright/test";
import * as path from "path";
import { glideClick, parkPointer, showPointer, startScreencast } from "./media/screencast";

const RECORD = process.env.RECORD_MEDIA === "1";
const FRAMES_ROOT = process.env.RECORD_FRAMES_DIR ?? path.resolve(__dirname, "../test-results/media-frames");
const BASE_URL = process.env.E2E_BASE_URL ?? "http://localhost:3000";

type User = { email: string; password: string };
const TOUR: User = { email: "tour@example.com", password: "tour123" };
const ALICE: User = { email: "alice.johnson@gmail.com", password: "customer123" };
const ADMIN: User = { email: "admin.demo@gmail.com", password: "admin123" };
const VIEWPORT = { width: 1280, height: 800 };
const SCALE = Number(process.env.RECORD_SCALE ?? 2);
const STOP = 'button[aria-label="Stop"], button:has(svg.lucide-square)';

// Recorded prompts, verbatim: a reworded prompt is a replay miss (manifest.json).
const PROMPTS = {
  search: "Show me noise-cancelling headphones under $350.",
  reviews: "Which of those has the best reviews?",
  addToCart: "Add the Sony WH-1000XM5 to my cart.",
  latestOrder: "Where's my latest order?",
  returnStart: "I want to return my last order",
  returnReason: "The cooler stopped working properly. Please refund it to my original payment method.",
  returnConfirm: "Yes, please go ahead.",
  dealAndStock: "Is the Sony WH-1000XM5 a good deal right now, and is it in stock?",
  prePurchase: "I am thinking of buying the Sony WH-1000XM5. Is it a good time to buy?",
};
const ALICE_LATEST_ORDER = "9dee7fee-d86b-5cca-a912-4987f224d562";

test.skip(!RECORD, "media capture: set RECORD_MEDIA=1 (scripts/record-media.sh does)");
test.setTimeout(300_000);
test.describe.configure({ mode: "serial" });

const beat = (page: Page, ms = 1_000) => page.waitForTimeout(ms);
const take = (name: string) => path.join(FRAMES_ROOT, name);

/** Space out the structural SSE frames: the workflow modes finish in milliseconds on a replay stack. */
function paceStream(page: Page, delayMs: number) {
  return page.addInitScript((delay: number) => {
    const realFetch = window.fetch.bind(window);
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const res = await realFetch(input, init);
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      if (!url.includes("/api/chat/stream") || !res.body) return res;
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      const encoder = new TextEncoder();
      let buffer = "";
      const stream = new ReadableStream<Uint8Array>({
        async pull(controller) {
          for (;;) {
            const cut = buffer.indexOf("\n\n");
            if (cut >= 0) {
              const frame = buffer.slice(0, cut + 2);
              buffer = buffer.slice(cut + 2);
              controller.enqueue(encoder.encode(frame));
              if (!frame.startsWith("data:") || frame.includes('"node')) {
                await new Promise((r) => setTimeout(r, delay));
              }
              return;
            }
            const { done, value } = await reader.read();
            if (done) {
              if (buffer) controller.enqueue(encoder.encode(buffer));
              buffer = "";
              controller.close();
              return;
            }
            buffer += decoder.decode(value, { stream: true });
          }
        },
        cancel: () => reader.cancel(),
      });
      return new Response(stream, { status: res.status, statusText: res.statusText, headers: res.headers });
    };
  }, delayMs);
}

async function signIn(page: Page, user: User) {
  await page.goto("/login");
  await page.evaluate(() => localStorage.clear());
  await page.goto("/login");
  await page.fill('input[type="email"]', user.email);
  await page.fill('input[type="password"]', user.password);
  await page.getByRole("button", { name: /^sign in$/i }).click();
  await page.waitForURL(/\/(chat|home)/, { timeout: 20_000 });
}

/**
 * A recording page for `user`: signed in on an unrecorded context (the clip never shows a login form), then a fresh
 * context at the clip's size and scale carrying that session, with the pointer and stream pacing installed.
 */
async function stage(browser: Browser, user: User, opts: { pace?: number; resetTour?: boolean } = {}) {
  const setup = await browser.newContext({ baseURL: BASE_URL, viewport: VIEWPORT });
  const s = await setup.newPage();
  await signIn(s, user);
  if (opts.resetTour) {
    const status = await s.evaluate(async () => {
      const token = localStorage.getItem("ecommerce_access_token");
      return (await fetch("/api/demo/reset", { method: "POST", headers: { Authorization: `Bearer ${token}` } })).status;
    });
    expect(status, "POST /api/demo/reset: is this the no-key stack (DEMO_TOUR_ENABLED)?").toBe(200);
  }
  const storageState = await setup.storageState();
  await setup.close();
  const context = await browser.newContext({
    baseURL: BASE_URL, viewport: VIEWPORT, deviceScaleFactor: SCALE, storageState, colorScheme: "light",
  });
  const page = await context.newPage();
  await showPointer(page);
  if (opts.pace) await paceStream(page, opts.pace);
  return { context, page };
}

const answerCount = (page: Page) => page.locator('[data-message-role="assistant"]').count();

/**
 * Wait until the turn has answered. Not "until the stop button appears": a replayed answer can finish before that
 * check runs, and waiting it out left seconds of dead air in the clip. A turn is done when no stop button shows
 * and the conversation holds more assistant messages than before it was sent.
 */
async function waitForTurn(page: Page, answersBefore: number) {
  await page.waitForFunction(
    ({ stop, before }) =>
      !document.querySelector(stop) &&
      document.querySelectorAll('[data-message-role="assistant"]').length > before,
    { stop: STOP, before: answersBefore },
    { timeout: 120_000 },
  );
  await expect(page.getByTestId("replay-miss-callout"), "a prompt missed its recording").toHaveCount(0);
}

/** Type a prompt the way a person does (about 45 characters a second), send it, wait for the answer. */
async function ask(page: Page, prompt: string) {
  const input = page.locator("textarea").first();
  await input.waitFor({ state: "visible", timeout: 15_000 });
  await glideClick(page, input);
  await input.pressSequentially(prompt, { delay: 22 });
  await beat(page, 400);
  const before = await answerCount(page);
  await input.press("Enter");
  await parkPointer(page);
  await waitForTurn(page, before);
}

async function openChat(page: Page, route = "/chat") {
  await page.goto(route);
  await page.waitForLoadState("networkidle").catch(() => {});
  await expect(page.locator("textarea").first()).toBeVisible();
  await beat(page, 600);
}

/** Scroll the chat so the newest answer's top is in view, the way a reader would before reading it. */
async function showLatestAnswer(page: Page) {
  await page.getByTestId("assistant-text").last().scrollIntoViewIfNeeded().catch(() => {});
}

// ── Scenarios ─────────────────────────────────────────────────────────────────────────────────────────────────────

test("order: where is my order", async ({ browser }) => {
  const { context, page } = await stage(browser, ALICE);
  await openChat(page);
  const rec = await startScreencast(page, take("order"));
  await beat(page, 700);
  await ask(page, PROMPTS.latestOrder);
  await beat(page, 3_500);
  console.log("order", await rec.stop());
  await context.close();
});

test("multi: one question, two specialists, two cards", async ({ browser }) => {
  const { context, page } = await stage(browser, ALICE);
  await openChat(page);
  const rec = await startScreencast(page, take("multi"));
  await beat(page, 700);
  await ask(page, PROMPTS.dealAndStock);
  await showLatestAnswer(page);
  await beat(page, 2_500);
  // Read down through both cards.
  await page.mouse.move(900, 450);
  for (let i = 0; i < 6; i++) {
    await page.mouse.wheel(0, 220);
    await beat(page, 650);
  }
  await beat(page, 2_000);
  console.log("multi", await rec.stop());
  await context.close();
});

test("research: is now a good time to buy", async ({ browser }) => {
  const { context, page } = await stage(browser, TOUR, { resetTour: true, pace: 500 });
  await openChat(page);
  const rec = await startScreencast(page, take("research"));
  await beat(page, 700);
  // The tour chip carries the research workflow's mode; the shopper sees one combined answer.
  const before = await answerCount(page);
  await glideClick(page, page.getByRole("button", { name: "Is now a good time to buy the XM5?" }));
  await parkPointer(page);
  await waitForTurn(page, before);
  await beat(page, 3_500);
  console.log("research", await rec.stop());
  await context.close();
});

test("lab: the same chat with its machinery shown", async ({ browser }) => {
  const { context, page } = await stage(browser, ADMIN, { pace: 600 });
  await openChat(page, "/lab");
  const rec = await startScreencast(page, take("lab"));
  await beat(page, 800);

  // 1. A tool-routed turn: which agents answered, every step, the grounding report.
  await ask(page, PROMPTS.dealAndStock);
  await beat(page, 1_500);
  const timeline = page.getByText(/agent activity/i).last();
  if (await timeline.isVisible().catch(() => false)) {
    await glideClick(page, timeline);
    await beat(page, 2_500);
  }
  await parkPointer(page);

  // 2. Switch to the research workflow: its graph draws and lights up as the run goes.
  const switcher = page.getByLabel("Orchestration mode");
  await expect(switcher, "the Lab has no mode switcher").toBeVisible();
  await glideClick(page, switcher);
  await beat(page, 700);
  await glideClick(page, page.getByRole("option", { name: /pre-purchase/i }).first());
  await beat(page, 600);
  await ask(page, PROMPTS.prePurchase);
  await beat(page, 3_000);

  // 3. Compare: one prompt through two modes, side by side, with latency, steps and cost.
  await glideClick(page, page.getByRole("button", { name: /^Compare/ }).first());
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  const box = dialog.locator("textarea");
  await glideClick(page, box);
  await box.pressSequentially(PROMPTS.prePurchase, { delay: 12 });
  // Only the workflow modes (no model) answer a fresh compare request on the no-key stack.
  const toolRouter = dialog.getByText("Tool Router", { exact: true });
  if (await toolRouter.isVisible().catch(() => false)) await glideClick(page, toolRouter);
  await glideClick(page, dialog.getByText(/^Pre-Purchase Research/));
  await glideClick(page, dialog.getByText(/^Return & Replace/));
  await glideClick(page, dialog.getByRole("button", { name: /run comparison/i }));
  await parkPointer(page);
  await expect(dialog.getByTestId(/^usage-/).first(), "the comparison returned nothing").toBeVisible({ timeout: 60_000 });
  await beat(page, 3_500);

  console.log("lab", await rec.stop());
  await context.close();
});

test("return: a return, staff approval, then the label", async ({ browser }) => {
  // 1. Alice asks for a return, gives a reason, and confirms the exact action before anything happens.
  const customer = await stage(browser, ALICE);
  await openChat(customer.page);
  const rec1 = await startScreencast(customer.page, take("return-1"));
  await beat(customer.page, 700);
  await ask(customer.page, PROMPTS.returnStart);
  await beat(customer.page, 2_200);
  await ask(customer.page, PROMPTS.returnReason);
  await beat(customer.page, 2_000);
  await ask(customer.page, PROMPTS.returnConfirm);
  await beat(customer.page, 2_500);
  console.log("return-1", await rec1.stop());
  await customer.context.close();

  // 2. A reviewer approves it in the inbox: the refund is over the threshold that needs a manager.
  const staff = await stage(browser, ADMIN);
  await staff.page.goto("/admin/approvals");
  await staff.page.waitForLoadState("networkidle").catch(() => {});
  const rec2 = await startScreencast(staff.page, take("return-2"));
  await beat(staff.page, 1_500);
  const approve = staff.page.getByRole("button", { name: /^approve$/i }).first();
  await expect(approve, "no return waiting in the approvals inbox").toBeVisible({ timeout: 15_000 });
  // Open the evidence first: why policy routed it to a manager (amount over 500, delivery date, refund ratio).
  const details = staff.page.getByText(/show details/i).first();
  if (await details.isVisible().catch(() => false)) {
    await glideClick(staff.page, details);
    await beat(staff.page, 3_000);
  }
  await glideClick(staff.page, approve);
  await parkPointer(staff.page);
  await beat(staff.page, 2_500);
  console.log("return-2", await rec2.stop());
  await staff.context.close();

  // 3. Alice's order now carries the return and a label to print.
  const after = await stage(browser, ALICE);
  await after.page.goto(`/orders/${ALICE_LATEST_ORDER}`);
  await after.page.waitForLoadState("networkidle").catch(() => {});
  const rec3 = await startScreencast(after.page, take("return-3"));
  await beat(after.page, 1_000);
  const label = after.page.getByText(/download return label/i).first();
  await expect(label, "the approved return has no label").toBeVisible({ timeout: 15_000 });
  await label.scrollIntoViewIfNeeded();
  await beat(after.page, 600);
  const box = await label.boundingBox();
  if (box) await after.page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 25 });
  await beat(after.page, 3_000);
  console.log("return-3", await rec3.stop());
  await after.context.close();
});

test("hero: search, compare reviews, add to cart, check out", async ({ browser }) => {
  const { context, page } = await stage(browser, TOUR, { resetTour: true });
  await openChat(page);
  const rec = await startScreencast(page, take("hero"));
  await beat(page, 700);

  await ask(page, PROMPTS.search);
  await beat(page, 2_400);
  await ask(page, PROMPTS.reviews);
  await beat(page, 2_600);
  await ask(page, PROMPTS.addToCart);
  await beat(page, 2_000);

  const checkout = page.getByRole("link", { name: /complete checkout/i }).last();
  await expect(checkout, "the checkout card did not render").toBeVisible();
  await glideClick(page, checkout);
  await page.waitForURL(/\/checkout/, { timeout: 15_000 });
  await page.waitForLoadState("networkidle").catch(() => {});
  await beat(page, 1_200);
  // The tour account has no saved address: fill one in, quickly, as autofill would.
  for (const [field, value] of [["street", "200 Market St"], ["city", "San Francisco"], ["state", "CA"], ["zip", "94105"]]) {
    const input = page.locator(`input[id$="-${field}"]`).first();
    if ((await input.inputValue().catch(() => "")) === "") await input.fill(value);
  }
  await beat(page, 600);
  const place = page.getByRole("button", { name: /place order/i });
  await place.scrollIntoViewIfNeeded();
  await glideClick(page, place);
  await page.waitForURL(/\/orders\//, { timeout: 20_000 }).catch(() => {});
  await page.waitForLoadState("networkidle").catch(() => {});
  await parkPointer(page);
  await beat(page, 3_000);

  console.log("hero", await rec.stop());
  await context.close();
});
