import AxeBuilder from "@axe-core/playwright";
import { test, expect, type Page } from "@playwright/test";
import { seedAuth, mockApi } from "./ui-mocks";

/**
 * Accessibility gate for every page that renders without a backend.
 *
 * axe-core runs against WCAG 2.0/2.1 A + AA and the spec fails on any
 * `serious` or `critical` violation. `minor`/`moderate` findings are not gated
 * (they are mostly best-practice advice, e.g. landmark structure), but they are
 * printed so they stay visible in the log.
 *
 * Rules are never disabled to get green. If a violation cannot be fixed in
 * scope it goes in KNOWN_EXCLUSIONS below, scoped to one selector, with the
 * reason and what would remove it. The list is empty today.
 *
 * Both themes are covered for the authenticated pages: colour contrast is where
 * a dark theme regresses first. Runs on the desktop and the mobile project
 * (see playwright.config.ts).
 */

type Exclusion = { selector: string; rule: string; reason: string };

const KNOWN_EXCLUSIONS: Exclusion[] = [];

async function scan(page: Page, label: string) {
  let builder = new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]);
  for (const ex of KNOWN_EXCLUSIONS) builder = builder.exclude(ex.selector);
  const { violations } = await builder.analyze();

  const blocking = violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  const advisory = violations.filter((v) => !blocking.includes(v));
  if (advisory.length > 0) {
    console.log(
      `[a11y] ${label}: ${advisory.length} non-blocking finding(s): ` +
        advisory.map((v) => `${v.id} (${v.impact})`).join(", "),
    );
  }

  expect(
    blocking.map((v) => ({
      rule: v.id,
      impact: v.impact,
      help: v.help,
      targets: v.nodes.slice(0, 5).map((n) => n.target.join(" ")),
    })),
    `${label}: serious/critical accessibility violations`,
  ).toEqual([]);
}

test.describe("public pages", () => {
  for (const [name, path] of [
    ["landing", "/"],
    ["login", "/login"],
    ["signup", "/signup"],
  ] as const) {
    test(`${name} has no serious or critical violations`, async ({ page }) => {
      await page.goto(path);
      await page.waitForLoadState("networkidle");
      await scan(page, name);
    });
  }

  test("not-found page has no serious or critical violations", async ({ page }) => {
    await page.goto("/definitely-not-a-route");
    await expect(page.getByRole("heading", { name: /page not found/i })).toBeVisible();
    await scan(page, "not-found");
  });
});

test.describe("authenticated shell", () => {
  for (const theme of ["light", "dark"] as const) {
    for (const [name, path, role] of [
      ["home", "/home", "customer"],
      ["products", "/products", "customer"],
      ["orders", "/orders", "customer"],
      ["admin usage", "/admin/usage", "admin"],
      ["admin decisions", "/admin/decisions", "admin"],
      ["admin returns", "/admin/returns", "admin"],
      ["seller proposals", "/seller/proposals", "seller"],
    ] as const) {
      test(`${name} (${theme}) has no serious or critical violations`, async ({ page }) => {
        await seedAuth(page, role, theme);
        await mockApi(page);
        await page.goto(path);
        await page.waitForLoadState("networkidle");
        await expect(page.getByRole("main")).toBeVisible();
        await scan(page, `${name} (${theme})`);
      });
    }
  }

  test("skip link is the first tab stop and moves focus to the main content", async ({
    page,
    isMobile,
  }) => {
    // Keyboard navigation: a touch-first device has no Tab key to press.
    test.skip(isMobile, "keyboard-only behaviour");
    await seedAuth(page);
    await mockApi(page);
    await page.goto("/home");
    await expect(page.getByRole("main")).toBeVisible();

    await page.keyboard.press("Tab");
    const skip = page.getByRole("link", { name: "Skip to main content" });
    await expect(skip).toBeFocused();
    await expect(skip).toBeVisible();

    await page.keyboard.press("Enter");
    await expect(page.locator("#main-content")).toBeFocused();
  });
});
