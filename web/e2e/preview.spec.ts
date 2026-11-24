/**
 * preview.spec.ts
 *
 * The static preview (#301) end to end: the Next.js static export served by a plain file server under the
 * GitHub Pages sub-path, no backend, no model, no key.
 *
 *   cd web && pnpm build:preview && PREVIEW_E2E=1 pnpm exec playwright test --project=preview
 *
 * (`PW_CHROMIUM_PATH` points Playwright at an installed headless shell when the pinned revision is absent.)
 * The chips come from the same committed file the preview serves, so editing the tour changes this test with it.
 *
 * Waiting: a streamed turn is finished when the composer's submit button stops showing the stop icon, never on
 * status text (CLAUDE.md, Testing & Evals).
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test, expect, type Page } from "@playwright/test";

interface Chip {
  id: string;
  label: string;
  prompt: string;
  new_conversation: boolean;
}

const data = JSON.parse(readFileSync(join(__dirname, "../src/lib/preview/preview-data.json"), "utf-8")) as {
  tour: { chips: Chip[]; miss_message: string };
};
const STOP = 'button[aria-label="Stop"], button:has(svg.lucide-square)';

test.setTimeout(180_000);

async function waitForTurn(page: Page): Promise<void> {
  await page.waitForSelector(STOP, { timeout: 10_000, state: "attached" }).catch(() => {});
  await page.waitForSelector(STOP, { timeout: 60_000, state: "detached" });
}

async function signIn(page: Page): Promise<void> {
  await page.goto("login/");
  // The form arrives filled in with the one synthetic account.
  await expect(page.locator('input[type="email"]')).toHaveValue("tour@example.com");
  await page.getByRole("button", { name: /sign in/i }).click();
  await page.waitForURL(/\/chat\/?$/, { timeout: 20_000 });
}

test("preview: banner, sign-in without a backend, every tour chip answers, an unrecorded prompt is declined", async ({
  page,
}) => {
  const backendCalls: string[] = [];
  page.on("request", (req) => {
    const url = new URL(req.url());
    if (url.pathname.includes("/api/")) backendCalls.push(`${req.method()} ${url.pathname}`);
  });

  await signIn(page);
  await expect(page.getByTestId("preview-banner")).toContainText("Preview: recorded answers");

  for (const chip of data.tour.chips) {
    await test.step(chip.id, async () => {
      const composer = page.locator("textarea").first();
      if (chip.new_conversation) {
        await page.goto("chat/");
        const button = page.getByTestId("demo-tour-chips").getByRole("button", { name: chip.label, exact: true });
        await expect(button).toBeVisible({ timeout: 15_000 });
        await button.click();
      } else {
        await composer.fill(chip.prompt);
        await composer.press("Enter");
      }
      await expect(page.getByText(chip.prompt).last()).toBeVisible({ timeout: 10_000 });
      await waitForTurn(page);

      await expect(page.getByTestId("replay-miss-callout")).toHaveCount(0);
      const answer = page.locator('[class*="prose"]').last();
      await expect(answer).toBeVisible();
      expect(((await answer.textContent()) ?? "").trim().length, `${chip.id} answered nothing`).toBeGreaterThan(20);

      if (chip.id === "return-approval") {
        await expect(
          page.getByRole("alert").filter({ hasText: /Awaiting staff review|needs your approval/i }).first(),
        ).toBeVisible({ timeout: 10_000 });
      }
    });
  }

  await test.step("an unrecorded prompt gets the polite notice", async () => {
    await page.goto("chat/");
    const composer = page.locator("textarea").first();
    await composer.fill("What is the airspeed velocity of an unladen swallow?");
    await composer.press("Enter");
    await waitForTurn(page);
    const callout = page.getByTestId("replay-miss-callout");
    await expect(callout).toBeVisible({ timeout: 10_000 });
    await expect(callout).toContainText("This preview replays recorded answers; run it locally for the real thing.");
    await expect(callout.getByRole("link", { name: "Quick start" })).toHaveAttribute("href", /quick-start/);
  });

  // The mock answers inside the browser: nothing may have gone out to a server path of the page's own origin.
  expect(backendCalls, "the preview must not call a backend").toEqual([]);
});

test("preview: the catalog and a product page render from the snapshot", async ({ page }) => {
  await signIn(page);
  await page.goto("products/");
  await expect(page.getByText("Sony WH-1000XM5").first()).toBeVisible({ timeout: 15_000 });
  await page.getByText("Sony WH-1000XM5").first().click();
  await expect(page).toHaveURL(/\/products\/[0-9a-f-]{36}\/?$/);
  await expect(page.getByRole("heading", { name: "Sony WH-1000XM5" })).toBeVisible({ timeout: 15_000 });
});
