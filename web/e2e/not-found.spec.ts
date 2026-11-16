import { test, expect } from "@playwright/test";

/**
 * An unknown route renders the app's own 404 page. Public route, no auth and
 * no API calls, so like ui-smoke.spec.ts this needs the frontend alone.
 */
test("unknown route shows the not-found page with a way home", async ({ page }) => {
  const response = await page.goto("/definitely-not-a-route");
  expect(response?.status()).toBe(404);
  await expect(page.getByRole("heading", { name: /page not found/i })).toBeVisible();
  const home = page.getByRole("link", { name: /back to home/i });
  await expect(home).toBeVisible();
  await home.click();
  await expect(page).toHaveURL(/\/$/);
});
