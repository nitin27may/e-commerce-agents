/**
 * demo-media.spec.ts
 *
 * The documentation clips, one test per scenario, recorded at high quality on the NO-KEY stack. No model key is
 * used: every answer is a recorded replay fixture (the tour user's chips) or a workflow mode that calls no model.
 *
 *   ./scripts/dev.sh --no-key                    # the stack
 *   scripts/record-media.sh                      # every scenario -> docs/images/<name>.gif + docs/media/<name>.mp4
 *   scripts/record-media.sh hero approval        # just these
 *
 * Skipped unless RECORD_MEDIA=1. Nothing here asserts product behaviour (no-key-tour.spec.ts does); a missing
 * control fails the take loudly instead of producing a clip that quietly lacks a beat.
 *
 * Every clip shares one frame: a 1280 x 800 viewport at device scale 2, light theme, the tour user, the pointer
 * visible. That is what makes the set read as one product. Capture is web/e2e/media/screencast.ts (lossless frames),
 * encoding is scripts/demo/encode.sh; the settings and the reasons for them are in scripts/demo/README.md.
 *
 * Pacing: the workflow modes finish in 100 to 300 ms on a replay stack, so the SSE frames of /api/chat/stream are
 * spaced out (paceStream). The frames are the backend's own; only their arrival is slowed, which is what a live
 * model's latency does anyway. Beats after each answer are long enough to read it once.
 */

import { test, expect, type Browser, type Page } from "@playwright/test";
import * as path from "path";
import { glideClick, parkPointer, showPointer, startScreencast } from "./media/screencast";

const RECORD = process.env.RECORD_MEDIA === "1";
const FRAMES_ROOT = process.env.RECORD_FRAMES_DIR ?? path.resolve(__dirname, "../test-results/media-frames");
const BASE_URL = process.env.E2E_BASE_URL ?? "http://localhost:3000";
// Node-side reads (the tour chips). In-page calls go through the frontend's own /api proxy, so they are same-origin.
const API = process.env.E2E_API_URL ?? "http://localhost:8080";

const TOUR = { email: "tour@example.com", password: "tour123" };
const ADMIN = { email: "admin.demo@gmail.com", password: "admin123" };
const VIEWPORT = { width: 1280, height: 800 };
const SCALE = Number(process.env.RECORD_SCALE ?? 2);
const STOP = 'button[aria-label="Stop"], button:has(svg.lucide-square)';

test.skip(!RECORD, "media capture: set RECORD_MEDIA=1 (scripts/record-media.sh does)");
test.setTimeout(300_000);
test.describe.configure({ mode: "serial" });

const beat = (page: Page, ms = 1_000) => page.waitForTimeout(ms);

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

async function signIn(page: Page, user: { email: string; password: string }) {
  await page.goto("/login");
  await page.evaluate(() => localStorage.clear());
  await page.goto("/login");
  await page.fill('input[type="email"]', user.email);
  await page.fill('input[type="password"]', user.password);
  await page.getByRole("button", { name: /log\s*in|sign\s*in/i }).click();
  await page.waitForURL(/\/chat/, { timeout: 20_000 });
}

async function resetDemo(page: Page) {
  const status = await page.evaluate(async () => {
    const token = localStorage.getItem("ecommerce_access_token");
    const res = await fetch("/api/demo/reset", { method: "POST", headers: { Authorization: `Bearer ${token}` } });
    return res.status;
  });
  expect(status, "POST /api/demo/reset: is this the no-key stack (DEMO_TOUR_ENABLED)?").toBe(200);
}

async function tourChips(): Promise<Record<string, string>> {
  const res = await fetch(`${API}/api/demo/tour`);
  expect(res.ok, `GET ${API}/api/demo/tour: is DEMO_TOUR_ENABLED set (docker-compose.nokey.yml)?`).toBe(true);
  const body = (await res.json()) as { chips: { id: string; prompt: string }[] };
  return Object.fromEntries(body.chips.map((c) => [c.id, c.prompt]));
}

/**
 * A recording page: signed in on an unrecorded context first (the clip never shows a login form), then a fresh
 * context at the clip's size and scale carrying that session, with the pointer and stream pacing installed.
 */
async function stage(browser: Browser, user = TOUR, pace = 450) {
  const setup = await browser.newContext({ baseURL: BASE_URL, viewport: VIEWPORT });
  const s = await setup.newPage();
  await signIn(s, user);
  if (user === TOUR) await resetDemo(s);
  const storageState = await setup.storageState();
  await setup.close();
  const context = await browser.newContext({
    baseURL: BASE_URL, viewport: VIEWPORT, deviceScaleFactor: SCALE, storageState, colorScheme: "light",
  });
  const page = await context.newPage();
  await showPointer(page);
  if (pace) await paceStream(page, pace);
  return { context, page };
}

async function waitForTurn(page: Page) {
  await page.waitForSelector(STOP, { timeout: 20_000, state: "attached" }).catch(() => {});
  await page.waitForSelector(STOP, { timeout: 120_000, state: "detached" });
  await expect(page.getByTestId("replay-miss-callout"), "a prompt missed its recording").toHaveCount(0);
}

async function ask(page: Page, prompt: string) {
  const input = page.locator("textarea").first();
  await input.waitFor({ state: "visible", timeout: 15_000 });
  await glideClick(page, input);
  // About 45 characters a second: fast enough not to bore, slow enough to read along.
  await input.pressSequentially(prompt, { delay: 22 });
  await beat(page, 400);
  await input.press("Enter");
  await parkPointer(page);
  await waitForTurn(page);
}

async function pickMode(page: Page, option: RegExp) {
  const switcher = page.getByLabel("Orchestration mode");
  await expect(switcher, "the mode switcher is missing").toBeVisible({ timeout: 10_000 });
  await glideClick(page, switcher);
  await beat(page, 800);
  await glideClick(page, page.getByRole("option", { name: option }).first());
  await beat(page, 600);
}

async function openChat(page: Page) {
  await page.goto("/chat");
  await page.waitForLoadState("networkidle").catch(() => {});
  await expect(page.locator("textarea").first()).toBeVisible();
  await beat(page, 600);
}

const take = (name: string) => path.join(FRAMES_ROOT, name);

// ── Scenarios ──────────────────────────────────────────────────────────────────

test("hero: question, cards, mode switch, live graph, approval pause", async ({ browser }) => {
  const chips = await tourChips();
  const { context, page } = await stage(browser);
  await openChat(page);
  const rec = await startScreencast(page, take("hero"));
  await beat(page, 700);

  await ask(page, chips["search-headphones"]);
  await beat(page, 2_400);
  await pickMode(page, /pre-purchase/i);
  await ask(page, chips["pre-purchase"]);
  await beat(page, 2_400);
  await pickMode(page, /return\s*&\s*replace/i);
  await ask(page, chips["return-approval"]);
  const pause = page.getByRole("alert").filter({ hasText: /Awaiting staff review|needs your approval/i });
  await expect(pause.first(), "the return did not pause for approval").toBeVisible({ timeout: 10_000 });
  await pause.first().scrollIntoViewIfNeeded();
  await beat(page, 3_000);

  console.log("hero", await rec.stop());
  await context.close();
});

test("generative-ui: cards, then a follow-up that keeps the context", async ({ browser }) => {
  const chips = await tourChips();
  const { context, page } = await stage(browser);
  await openChat(page);
  const rec = await startScreencast(page, take("generative-ui"));
  await beat(page, 700);
  await ask(page, chips["search-headphones"]);
  await beat(page, 2_600);
  await ask(page, chips["follow-up-compare"]);
  await beat(page, 3_000);
  console.log("generative-ui", await rec.stop());
  await context.close();
});

test("group-chat: two specialists debate, a moderator answers", async ({ browser }) => {
  const chips = await tourChips();
  const { context, page } = await stage(browser, TOUR, 700);
  await openChat(page);
  const rec = await startScreencast(page, take("group-chat"));
  await beat(page, 700);
  await pickMode(page, /group|round/i);
  await ask(page, chips["group-chat"]);
  await beat(page, 3_500);
  console.log("group-chat", await rec.stop());
  await context.close();
});

test("handoff: triage hands the conversation to the order specialist", async ({ browser }) => {
  const chips = await tourChips();
  const { context, page } = await stage(browser, TOUR, 600);
  await openChat(page);
  const rec = await startScreencast(page, take("handoff"));
  await beat(page, 700);
  await pickMode(page, /handoff/i);
  await ask(page, chips["handoff-order"]);
  await beat(page, 3_000);
  console.log("handoff", await rec.stop());
  await context.close();
});

test("approval: a return pauses, staff approve it on /runs, the run resumes", async ({ browser }) => {
  const chips = await tourChips();
  // Customer half: park the return. Recorded, so the clip shows why there is something to approve.
  const customer = await stage(browser);
  await openChat(customer.page);
  const rec1 = await startScreencast(customer.page, take("approval-1"));
  await beat(customer.page, 600);
  await pickMode(customer.page, /return\s*&\s*replace/i);
  await ask(customer.page, chips["return-approval"]);
  const pause = customer.page.getByRole("alert").filter({ hasText: /Awaiting staff review|needs your approval/i });
  await expect(pause.first(), "the return did not pause for approval").toBeVisible({ timeout: 10_000 });
  await beat(customer.page, 2_500);
  console.log("approval-1", await rec1.stop());
  await customer.context.close();

  // Staff half: the same run on /runs, approved, resumed from its checkpoint.
  const staff = await stage(browser, ADMIN, 0);
  await staff.page.goto("/runs");
  await staff.page.waitForLoadState("networkidle").catch(() => {});
  const rec2 = await startScreencast(staff.page, take("approval-2"));
  await beat(staff.page, 1_200);
  const needs = staff.page.getByText(/Needs approval/i).first();
  await expect(needs, "no paused run on /runs").toBeVisible({ timeout: 15_000 });
  await glideClick(staff.page, needs);
  await beat(staff.page, 1_500);
  const approve = staff.page.getByRole("button", { name: /^approve/i }).first();
  await expect(approve).toBeVisible({ timeout: 10_000 });
  await glideClick(staff.page, approve);
  await beat(staff.page, 800);
  // A confirm step, when there is one.
  const confirm = staff.page.getByRole("dialog").getByRole("button", { name: /approve|confirm/i }).first();
  if (await confirm.isVisible().catch(() => false)) await glideClick(staff.page, confirm);
  await parkPointer(staff.page);
  await beat(staff.page, 3_500);
  console.log("approval-2", await rec2.stop());
  await staff.context.close();
});

test("compare: one prompt through two orchestration modes, side by side", async ({ browser }) => {
  const chips = await tourChips();
  const { context, page } = await stage(browser, TOUR, 0);
  await openChat(page);
  const rec = await startScreencast(page, take("compare"));
  await beat(page, 700);
  await glideClick(page, page.getByRole("button", { name: /^Compare/ }).first());
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await beat(page, 600);
  const box = dialog.locator("textarea");
  await glideClick(page, box);
  await box.pressSequentially(chips["pre-purchase"], { delay: 18 });
  // Only the workflow modes (no model) can answer on the no-key stack. Tool Router is preselected: drop it.
  await glideClick(page, dialog.getByText("Tool Router", { exact: true }));
  await glideClick(page, dialog.getByText(/^Pre-Purchase Research/));
  await glideClick(page, dialog.getByText(/^Return & Replace/));
  await beat(page, 400);
  await glideClick(page, dialog.getByRole("button", { name: /Run comparison/ }));
  await parkPointer(page);
  await expect(dialog.getByTestId(/^usage-/).first()).toBeVisible({ timeout: 60_000 });
  await beat(page, 3_500);
  console.log("compare", await rec.stop());
  await context.close();
});
