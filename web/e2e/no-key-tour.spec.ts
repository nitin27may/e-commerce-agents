/**
 * no-key-tour.spec.ts
 *
 * The scripted demo tour (agents/python/config/demo/tour.yaml) run end to end against the no-key stack
 * (issue #86: "the scripted tour completes including one approval, and a Playwright spec runs it in CI").
 *
 *   ./scripts/dev.sh --no-key            # from source: docker-compose.yml + docker-compose.nokey.yml
 *   cd web && pnpm exec playwright test e2e/no-key-tour.spec.ts --project=chromium-python
 *
 * No model is called: the stack answers from recorded fixtures (LLM_PROVIDER=replay). The chips are read from
 * the same `GET /api/demo/tour` the chat page renders, which serves tour.yaml, so editing the tour changes
 * this test with it. For every chip the spec asserts a non-empty answer that is not the "not recorded" notice;
 * for the return chip it asserts the approval pause (a customer sees "Awaiting staff review") and that
 * nothing was created: the delivered order is still delivered and no return exists for it.
 *
 * Waiting: a streamed turn is finished when the composer's submit button stops showing the stop icon. Never
 * wait on status text such as "Routing to specialists...", which disappears mid-turn (CLAUDE.md, Testing & Evals).
 */

import { test, expect, type Page } from "@playwright/test";

const TOUR_USER = { email: "tour@example.com", password: "tour123" };
const API = process.env.E2E_API_URL ?? "http://localhost:8080";
const STOP = 'button[aria-label="Stop"], button:has(svg.lucide-square)';

interface Chip {
  id: string;
  label: string;
  prompt: string;
  mode: string;
  new_conversation: boolean;
}

test.setTimeout(600_000);

async function login(page: Page): Promise<void> {
  await page.goto("/login");
  await page.evaluate(() => {
    localStorage.removeItem("ecommerce_user");
    localStorage.removeItem("ecommerce_access_token");
    localStorage.removeItem("ecommerce_refresh_token");
  });
  await page.goto("/login");
  await page.fill('input[type="email"]', TOUR_USER.email);
  await page.fill('input[type="password"]', TOUR_USER.password);
  await page.getByRole("button", { name: /log\s*in|sign\s*in/i }).click();
  await page.waitForURL(/\/chat/, { timeout: 20_000 });
}

/** Wait for the in-flight turn to finish, on the stop icon and nothing else. */
async function waitForTurn(page: Page): Promise<void> {
  await page.waitForSelector(STOP, { timeout: 20_000, state: "attached" }).catch(() => {});
  await page.waitForSelector(STOP, { timeout: 180_000, state: "detached" });
}

async function apiCall(page: Page, method: string, path: string): Promise<{ status: number; body: unknown }> {
  return page.evaluate(
    async ([base, m, p]) => {
      const token = localStorage.getItem("ecommerce_access_token");
      const res = await fetch(`${base}${p}`, { method: m, headers: { Authorization: `Bearer ${token}` } });
      const body = await res.json().catch(() => null);
      return { status: res.status, body };
    },
    [API, method, path],
  );
}

/** The last assistant answer's rendered text. */
async function lastAnswer(page: Page): Promise<string> {
  const rendered = page.locator('[class*="prose"]').last();
  await expect(rendered).toBeVisible({ timeout: 10_000 });
  return ((await rendered.textContent()) ?? "").trim();
}

test("no-key tour: every chip answers from a recording, the return pauses for approval", async ({ page, request }) => {
  const tourRes = await request.get(`${API}/api/demo/tour`);
  expect(
    tourRes.ok(),
    "GET /api/demo/tour must serve the tour: is DEMO_TOUR_ENABLED set (docker-compose.nokey.yml)?",
  ).toBe(true);
  const tour = (await tourRes.json()) as { chips: Chip[]; miss_message: string };
  expect(tour.chips.length).toBeGreaterThanOrEqual(9);
  const missText = tour.miss_message.slice(0, 40);

  await login(page);
  // Start from the seeded state so a rerun against the same stack takes the same path
  // (a return can be started once per order).
  const reset = await apiCall(page, "POST", "/api/demo/reset");
  expect(reset.status).toBe(200);

  for (const chip of tour.chips) {
    await test.step(`${chip.id} (${chip.mode})`, async () => {
      const composer = page.locator("textarea").first();
      if (chip.new_conversation) {
        await page.goto("/chat");
        const button = page.getByTestId("demo-tour-chips").getByRole("button", { name: chip.label, exact: true });
        await expect(button).toBeVisible({ timeout: 15_000 });
        await button.click();
      } else {
        // A follow-up continues the previous chip's conversation; the chips are not on screen then,
        // so send the prompt as typed text. The composer keeps the previous chip's mode.
        await composer.fill(chip.prompt);
        await composer.press("Enter");
      }
      await expect(page.getByText(chip.prompt).last()).toBeVisible({ timeout: 10_000 });
      await waitForTurn(page);

      await expect(page.getByTestId("replay-miss-callout"), `${chip.id} was not recorded`).toHaveCount(0);
      const answer = await lastAnswer(page);
      expect(answer.length, `${chip.id} answered nothing`).toBeGreaterThan(20);
      expect(answer).not.toContain(missText);

      if (chip.id === "return-approval") {
        const pause = page.getByRole("alert").filter({ hasText: /Awaiting staff review|needs your approval/i });
        await expect(pause.first()).toBeVisible({ timeout: 10_000 });
        // Nothing is created before a person decides: the order is untouched and has no return.
        const orderId = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/.exec(chip.prompt)?.[0];
        expect(orderId, "the return chip must name its order by id").toBeTruthy();
        const order = await apiCall(page, "GET", `/api/orders/${orderId}`);
        expect(order.status).toBe(200);
        expect(JSON.stringify(order.body)).toContain('"delivered"');
        expect(JSON.stringify(order.body)).not.toMatch(/return_id|return_status|"returns":\[\{/);
      }
    });
  }
});
