/**
 * readme-gif.spec.ts
 *
 * The README hero clip and the documentation stills, recorded against the NO-KEY stack. No model key is
 * needed and none is used: every answer is a recorded replay fixture or a workflow mode that calls no model.
 *
 *   ./scripts/dev.sh --no-key        # the stack (docker-compose.yml + docker-compose.nokey.yml)
 *   scripts/record-demo.sh           # this spec, then video -> GIF (ffmpeg)
 *
 * Never part of the default run: every test is skipped unless RECORD_MEDIA=1, which scripts/record-demo.sh sets.
 * Nothing here asserts product behaviour (no-key-tour.spec.ts does that); a missing control fails the take
 * loudly instead of producing a clip that quietly lacks a beat.
 *
 * The clip is a take of the tour user (tour@example.com), the one account whose answers are recorded and whose
 * data is synthetic: ask a question -> specialist answers with cards -> switch mode from the composer -> the
 * graph animates -> a return pauses at the approval gate.
 *
 * ── Pacing ───────────────────────────────────────────────────────────────────
 * The two workflow modes call no model and finish in about 100 to 300 ms, so on a replay stack the graph would
 * flash by between two video frames. `paceStream` wraps the browser's fetch so the SSE frames of a
 * /api/chat/stream response reach the page one at a time, FRAME_DELAY_MS apart. The frames and their content are
 * the backend's own; only their arrival is spaced out, which is what a live model's latency does anyway.
 *
 * ── Waiting ──────────────────────────────────────────────────────────────────
 * A streamed turn is finished when the composer's stop icon goes away. Never wait on status text.
 */

import { test, expect, type Browser, type BrowserContext, type Page } from "@playwright/test";
import * as fs from "fs";
import * as path from "path";

const RECORD = process.env.RECORD_MEDIA === "1";
const OUT_DIR = path.resolve(__dirname, "../../docs/images");
// scripts/record-demo.sh points this at a scratch directory; the raw video never lands in the repo.
const VIDEO_DIR = process.env.RECORD_VIDEO_DIR ?? path.resolve(__dirname, "../test-results/readme-gif");
const BASE_URL = process.env.E2E_BASE_URL ?? "http://localhost:3000";
const API = process.env.E2E_API_URL ?? "http://localhost:8080";

const TOUR = { email: "tour@example.com", password: "tour123" };
// Staff decide approvals; the customer who asked for the return cannot. Seeded demo account, synthetic data.
const ADMIN = { email: "admin.demo@gmail.com", password: "admin123" };

const STOP = 'button[aria-label="Stop"], button:has(svg.lucide-square)';
const FRAME_DELAY_MS = 450;

// The width the clip is recorded at. The GIF is scaled to 960, so keep this close to it: a 1440 px capture
// scaled to 960 makes 14 px chat text about 9 px.
const CLIP = { width: 1200, height: 720 };
const STILL = { width: 1440, height: 900 };

test.skip(!RECORD, "media capture: set RECORD_MEDIA=1 (scripts/record-demo.sh does)");
test.setTimeout(300_000);
test.describe.configure({ mode: "serial" });

const beat = (page: Page, ms = 1_000) => page.waitForTimeout(ms);

/** Space the SSE frames of chat streams out. Installed before any page script runs. */
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
              // Text deltas are already incremental; only pace the structural frames.
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
  await page.evaluate(() => {
    localStorage.removeItem("ecommerce_user");
    localStorage.removeItem("ecommerce_access_token");
    localStorage.removeItem("ecommerce_refresh_token");
  });
  await page.goto("/login");
  await page.fill('input[type="email"]', user.email);
  await page.fill('input[type="password"]', user.password);
  await page.getByRole("button", { name: /log\s*in|sign\s*in/i }).click();
  await page.waitForURL(/\/chat/, { timeout: 20_000 });
  await page.waitForLoadState("networkidle").catch(() => {});
}

async function resetDemo(page: Page) {
  const status = await page.evaluate(async (base) => {
    const token = localStorage.getItem("ecommerce_access_token");
    const res = await fetch(`${base}/api/demo/reset`, { method: "POST", headers: { Authorization: `Bearer ${token}` } });
    return res.status;
  }, API);
  expect(status, "POST /api/demo/reset: is this the no-key stack (DEMO_TOUR_ENABLED)?").toBe(200);
}

async function waitForTurn(page: Page) {
  await page.waitForSelector(STOP, { timeout: 20_000, state: "attached" }).catch(() => {});
  await page.waitForSelector(STOP, { timeout: 120_000, state: "detached" });
  await expect(page.getByTestId("replay-miss-callout")).toHaveCount(0);
}

async function typeAndSend(page: Page, message: string, delay = 14) {
  const input = page.locator("textarea").first();
  await input.waitFor({ state: "visible", timeout: 15_000 });
  await input.click();
  await input.pressSequentially(message, { delay });
  await beat(page, 350);
  await input.press("Enter");
}

async function pickMode(page: Page, option: RegExp) {
  const switcher = page.getByLabel("Orchestration mode");
  await expect(switcher, "the mode switcher is missing").toBeVisible({ timeout: 10_000 });
  await switcher.click();
  await beat(page, 700);
  await page.getByRole("option", { name: option }).first().click();
  await beat(page, 600);
}

/** Tour prompts, read from the same endpoint the chat renders so editing tour.yaml cannot desync this spec. */
async function tourChips(): Promise<Record<string, { label: string; prompt: string }>> {
  const res = await fetch(`${API}/api/demo/tour`);
  expect(res.ok, "GET /api/demo/tour: is DEMO_TOUR_ENABLED set (docker-compose.nokey.yml)?").toBe(true);
  const body = (await res.json()) as { chips: { id: string; label: string; prompt: string }[] };
  return Object.fromEntries(body.chips.map((c) => [c.id, { label: c.label, prompt: c.prompt }]));
}

async function freshPage(
  browser: Browser,
  opts: { video?: boolean; size: { width: number; height: number }; scale?: number } ,
): Promise<{ context: BrowserContext; page: Page }> {
  const context = await browser.newContext({
    baseURL: BASE_URL,
    viewport: opts.size,
    deviceScaleFactor: opts.scale ?? 1,
    ...(opts.video ? { recordVideo: { dir: VIDEO_DIR, size: opts.size } } : {}),
  });
  const page = await context.newPage();
  return { context, page };
}

test("hero clip: question, cards, mode switch, graph, approval pause", async ({ browser }) => {
  fs.mkdirSync(VIDEO_DIR, { recursive: true });
  const chips = await tourChips();

  // Sign in on a context that is not recording, then carry the session into the recorded one, so the
  // clip starts on the chat and never shows a login form.
  const setup = await freshPage(browser, { size: CLIP });
  await signIn(setup.page, TOUR);
  await resetDemo(setup.page);
  const state = await setup.context.storageState();
  await setup.context.close();

  const context = await browser.newContext({
    baseURL: BASE_URL,
    viewport: CLIP,
    storageState: state,
    recordVideo: { dir: VIDEO_DIR, size: CLIP },
  });
  const page = await context.newPage();
  await paceStream(page, FRAME_DELAY_MS);

  await page.goto("/chat");
  await page.waitForLoadState("networkidle").catch(() => {});
  await beat(page, 800);

  // 1. A question; the answer renders as product cards (a recorded specialist answer, no model).
  await typeAndSend(page, chips["search-headphones"].prompt);
  await waitForTurn(page);
  await beat(page, 2_200);

  // 2. Switch the orchestration mode from the composer; a graph mode draws and animates its graph.
  await pickMode(page, /pre-purchase/i);
  await typeAndSend(page, chips["pre-purchase"].prompt);
  await waitForTurn(page);
  await beat(page, 2_200);

  // 3. A return in the return-and-replace workflow pauses at the policy gate; nothing is created.
  await pickMode(page, /return\s*&\s*replace/i);
  await typeAndSend(page, chips["return-approval"].prompt);
  await waitForTurn(page);
  const pause = page.getByRole("alert").filter({ hasText: /Awaiting staff review|needs your approval/i });
  await expect(pause.first(), "the return did not pause for approval").toBeVisible({ timeout: 10_000 });
  await pause.first().scrollIntoViewIfNeeded();
  await beat(page, 3_000);

  const video = page.video();
  await context.close(); // flushes the video file
  const written = video ? await video.path() : "";
  expect(written, "no video was recorded").not.toBe("");
  fs.copyFileSync(written, path.join(VIDEO_DIR, "hero.webm"));
});

/** A signed-in, reset tour-user page at still size (the session is carried over from an unrecorded context). */
async function tourPage(browser: Browser, pace?: number) {
  const setup = await freshPage(browser, { size: STILL });
  await signIn(setup.page, TOUR);
  await resetDemo(setup.page);
  const state = await setup.context.storageState();
  await setup.context.close();
  const context = await browser.newContext({ baseURL: BASE_URL, viewport: STILL, storageState: state });
  const page = await context.newPage();
  if (pace) await paceStream(page, pace);
  await page.goto("/chat");
  await page.waitForLoadState("networkidle").catch(() => {});
  return { context, page };
}

test("stills: live graph mid-run", async ({ browser }) => {
  test.setTimeout(90_000);
  const chips = await tourChips();
  const { context, page } = await tourPage(browser, 1_100);
  await pickMode(page, /pre-purchase/i);
  await typeAndSend(page, chips["pre-purchase"].prompt, 0);
  await page.waitForSelector(STOP, { timeout: 20_000, state: "attached" });
  const graph = page.locator("svg").filter({ has: page.locator("g.node") }).first();
  await expect(graph, "the orchestration graph never rendered").toBeVisible({ timeout: 20_000 });
  // Mid-run: the stop icon is still up and the first nodes have finished, the rest have not.
  await beat(page, 2_300);
  await expect(page.locator(STOP).first(), "the run finished before the shot").toBeVisible();
  fs.mkdirSync(OUT_DIR, { recursive: true });
  await page.screenshot({ path: path.join(OUT_DIR, "orchestration-graph-live.png") });
  await context.close();
});

test("stills: compare dialog", async ({ browser }) => {
  test.setTimeout(120_000);
  const chips = await tourChips();
  const { context, page } = await tourPage(browser);
  await page.getByRole("button", { name: /^Compare/ }).first().click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await dialog.locator("textarea").fill(chips["pre-purchase"].prompt);
  // Compare calls each model-backed mode with a request of its own, which no recording matches, so on the
  // no-key stack only the two workflow modes (no model) can answer. Tool Router is preselected: drop it.
  await dialog.getByText("Tool Router", { exact: true }).click();
  await dialog.getByText(/^Pre-Purchase Research/).click();
  await dialog.getByText(/^Return & Replace/).click();
  await dialog.getByRole("button", { name: /Run comparison/ }).click();
  await expect(dialog.getByTestId(/^usage-/).first()).toBeVisible({ timeout: 60_000 });
  await beat(page, 1_500);
  await page.screenshot({ path: path.join(OUT_DIR, "compare-dialog.png") });
  await context.close();
});

test("stills: Aspire trace of a handoff turn", async ({ browser }) => {
  test.setTimeout(120_000);
  const chips = await tourChips();
  const dashboard = process.env.ASPIRE_URL ?? "http://localhost:18888";
  const { context, page } = await tourPage(browser);
  await pickMode(page, /handoff/i);
  await typeAndSend(page, chips["handoff-order"].prompt, 0);
  await waitForTurn(page);
  await context.close();

  const aspire = await freshPage(browser, { size: STILL });
  const a = aspire.page;
  await a.goto(`${dashboard}/traces`, { waitUntil: "networkidle" });
  // A chat turn whose trace crosses into a specialist service (the handoff mesh calls order-management).
  await a.getByRole("row").filter({ hasText: /POST \/api\/chat\/stream/ }).filter({ hasText: /order-management/ })
    .first().getByText(/POST \/api\/chat\/stream/).click();
  await expect(a.getByText(/Trace detail/)).toBeVisible({ timeout: 15_000 });
  await a.mouse.click(1406, 82); // dismiss the "endpoint is unsecured" banner (local dev, no auth)
  await beat(a, 1_200);
  // Collapse the request handler: its children are database calls that bury the agent spans.
  // The dashboard may have opened a detail panel for the click that dismissed the banner; close it first.
  const closePanel = a.locator("fluent-button[title*='Close' i], button[title*='Close' i]").first();
  if (await closePanel.isVisible().catch(() => false)) await closePanel.click();
  await beat(a, 500);
  const handler = a.getByText("fastapi.endpoint", { exact: true }).first();
  const box = await handler.boundingBox();
  if (box) await a.mouse.click(box.x - 28, box.y + box.height / 2);
  await beat(a, 500);
  // That click can also land on the row and open the panel again; the shot wants the full-width tree.
  if (await closePanel.isVisible().catch(() => false)) await closePanel.click();
  await a.mouse.move(700, 880);
  await beat(a, 800);
  await a.screenshot({ path: path.join(OUT_DIR, "aspire-trace.png") });
  await aspire.context.close();
});

test("stills: /runs approval as a reviewer", async ({ browser }) => {
  test.setTimeout(120_000);
  const chips = await tourChips();
  // Park a return first: the tour user's, through the return-and-replace workflow.
  const { context, page } = await tourPage(browser);
  await pickMode(page, /return\s*&\s*replace/i);
  await typeAndSend(page, chips["return-approval"].prompt, 0);
  await waitForTurn(page);
  await context.close();

  const staff = await freshPage(browser, { size: STILL });
  await signIn(staff.page, ADMIN);
  await staff.page.goto("/runs");
  await staff.page.waitForLoadState("networkidle").catch(() => {});
  const needs = staff.page.getByText(/Needs approval/i).first();
  await expect(needs, "no paused run on /runs").toBeVisible({ timeout: 15_000 });
  await needs.click();
  await expect(staff.page.getByRole("button", { name: /^approve/i }).first()).toBeVisible({ timeout: 10_000 });
  await beat(staff.page, 800);
  await staff.page.screenshot({ path: path.join(OUT_DIR, "runs-approval.png") });
  await staff.context.close();
});

