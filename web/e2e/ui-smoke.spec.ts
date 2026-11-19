import { test, expect, type Page } from "@playwright/test";
import { seedAuth, mockApi } from "./ui-mocks";

/**
 * UI smoke suite for the enhanced shell + Phase 2 surfaces.
 *
 * Auth and the backend API are mocked (localStorage session + request
 * interception, see ui-mocks.ts) so this runs against the frontend alone — no
 * live stack. CI runs it as the `web-ui-smoke` job in tests.yml, against a
 * production build, on both the desktop and the mobile project.
 */


/**
 * The sidebar is a permanent column from the lg breakpoint up and a drawer
 * below it. Returns the container the nav links live in, opening the drawer
 * first on a phone-sized viewport.
 */
async function navContainer(page: Page, isMobile: boolean) {
  if (!isMobile) return page.getByRole("complementary");
  await page.getByRole("button", { name: "Toggle menu" }).click();
  const drawer = page.getByRole("dialog");
  await expect(drawer).toBeVisible();
  return drawer;
}

test.describe("public", () => {
  test("landing renders hero, agents and CTA", async ({ page }) => {
    await page.goto("/");
    await expect(
      page.getByRole("heading", { name: /multi-agent platform/i }),
    ).toBeVisible();
    await expect(page.getByText("Meet the agents")).toBeVisible();
    await expect(
      page.getByText("Product Discovery", { exact: true }),
    ).toBeVisible();
    await expect(page.getByRole("link", { name: /try the demo/i })).toBeVisible();
  });

  test("login page renders the form", async ({ page }) => {
    await page.goto("/login");
    await expect(page.locator('input[type="email"]')).toBeVisible();
    await expect(page.locator('input[type="password"]')).toBeVisible();
    await expect(page.getByRole("button", { name: /sign in/i })).toBeVisible();
  });
});

test.describe("authenticated shell", () => {
  test("home dashboard renders greeting, quick prompts and orders", async ({ page }) => {
    await seedAuth(page);
    await mockApi(page);
    await page.goto("/home");
    await expect(page.getByText(/Good (morning|afternoon|evening), Alice/)).toBeVisible();
    // Quick prompts are derived from DEMO_SCENARIOS (web/src/lib/scenarios.ts),
    // so assert a label that actually exists rather than a hardcoded sentence
    // that drifted the moment the scenario list was edited.
    await expect(
      page.getByRole("link", { name: "Product Search" }).first(),
    ).toBeVisible();
    await expect(page.getByText("Recent Orders")).toBeVisible();
    await expect(page.getByText("Specialist Agents")).toBeVisible();
  });

  test("grouped sidebar shows admin nav (Usage, Audit) for admins", async ({ page, isMobile }) => {
    await seedAuth(page, "admin");
    await mockApi(page);
    // /home, not /chat. This test is about the SIDEBAR, and the sidebar renders
    // on every authenticated page — while /chat needs enough live data that a
    // mocked API sends it to an error boundary ("This page couldn't load"),
    // which then fails every assertion here for a reason that has nothing to do
    // with navigation.
    //
    // Worth noting separately: that the chat page hard-crashes rather than
    // degrading when an API returns an unexpected shape is a real robustness
    // gap, not a test artefact. Recorded in plan 20; not fixed here, because
    // widening an error boundary is not a navigation change.
    await page.goto("/home");
    await expect(page.getByRole("link", { name: "Chat" }).first()).toBeVisible();
    const nav = await navContainer(page, isMobile);
    await expect(nav.getByRole("link", { name: "Usage" })).toBeVisible();
    // No "Audit": /admin/audit duplicated /runs and its nav entry was removed
    // deliberately. src/lib/nav.test.ts asserts its absence, so expecting it
    // here made the two suites contradict each other — this e2e test simply
    // never caught up. The page itself still exists; only the link went.
    await expect(nav.getByRole("link", { name: "Audit" })).toHaveCount(0);
    // The agent marketplace was removed entirely.
    await expect(nav.getByRole("link", { name: "Marketplace" })).toHaveCount(0);
    await expect(nav.getByRole("link", { name: "Requests" })).toHaveCount(0);
  });

  test("buyers see only shop + account nav", async ({ page, isMobile }) => {
    await seedAuth(page, "customer");
    await mockApi(page);
    await page.goto("/home");
    // Scope to the sidebar — "Chat"/"Profile" also appear as the home "Open chat"
    // quick-prompt and the top-bar avatar (aria-label="Profile").
    const sidebar = await navContainer(page, isMobile);
    await expect(sidebar.getByRole("link", { name: "Chat", exact: true })).toBeVisible();
    await expect(sidebar.getByRole("link", { name: "Profile" })).toBeVisible();
    await expect(sidebar.getByRole("link", { name: "Marketplace" })).toHaveCount(0);
    await expect(sidebar.getByRole("link", { name: "My Agents" })).toHaveCount(0);
    await expect(sidebar.getByRole("link", { name: "Usage" })).toHaveCount(0);
  });

  test("command palette opens and navigates", async ({ page }) => {
    await seedAuth(page);
    await mockApi(page);
    await page.goto("/home");
    // Open via the top-bar search button (also covers the ⌘K integration).
    await page.getByRole("button", { name: /search/i }).click();
    const search = page.getByPlaceholder(/Search pages/);
    await expect(search).toBeVisible();
    await search.fill("products");
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/products/);
  });

  test("theme toggle switches to dark", async ({ page }) => {
    await seedAuth(page, "customer", "light");
    await mockApi(page);
    await page.goto("/home");
    const toggle = page.getByRole("button", { name: /switch to dark mode/i });
    await toggle.click();
    await expect(page.locator("html")).toHaveClass(/dark/);
  });

  test("admin usage renders KPIs and a chart", async ({ page }) => {
    await seedAuth(page, "admin");
    await mockApi(page);
    await page.goto("/admin/usage");
    await expect(
      page.getByRole("heading", { name: /usage analytics/i }),
    ).toBeVisible();
    await expect(page.getByText("Daily Activity")).toBeVisible();
    // recharts renders an SVG surface
    await expect(page.locator("svg.recharts-surface").first()).toBeVisible();
  });

  test("admin decisions page shows budget use, a reversible action and one waiting for approval", async ({ page }) => {
    await seedAuth(page, "admin");
    await mockApi(page);
    await page.goto("/admin/decisions");
    await expect(page.getByRole("heading", { name: "Autonomous decisions" })).toBeVisible();
    await expect(page.getByText(/1 of 20 actions and \$5\.00 of \$200\.00 used today/)).toBeVisible();
    await expect(page.getByText("Waiting for approval").first()).toBeVisible();
    await expect(page.getByRole("button", { name: "Reverse", exact: true })).toHaveCount(1);
    await expect(page.getByRole("button", { name: /large delay/ })).toBeVisible();
  });

  test("returns to inspect lists a delivered return and refuses an amount above the original", async ({ page }) => {
    await seedAuth(page, "admin");
    await mockApi(page);
    await page.goto("/admin/returns");
    await expect(page.getByRole("heading", { name: "Returns to inspect" })).toBeVisible();
    const card = page.getByTestId("inspection-card");
    await expect(card.getByText(/Alice Johnson/)).toBeVisible();
    await expect(card.getByText(/Arrived with a cracked screen/)).toBeVisible();
    await expect(card.getByRole("button", { name: "Accept" })).toBeEnabled();
    await expect(card.getByRole("button", { name: "Reject" })).toBeEnabled();
    // The amount can only go down; the page says so before asking the server.
    await card.getByLabel("Amount").fill("200");
    await card.getByRole("button", { name: "Accept" }).click();
    await expect(card.getByRole("alert")).toContainText("cannot be higher than the original $129.95");
  });

  test("seller proposals page lists suggestions, blocks a limited line and shows its guard reason", async ({ page }) => {
    await seedAuth(page, "seller");
    await mockApi(page);
    await page.goto("/seller/proposals");
    await expect(page.getByRole("heading", { name: "Proposals" })).toBeVisible();
    await expect(page.getByText("Clean Code")).toBeVisible();
    await expect(page.getByText(/Blocked: Margin would fall below the floor/)).toBeVisible();
    // A blocked line can only be rejected.
    const blocked = page.getByRole("group", { name: "Decision for Sony Alpha a6700" });
    await expect(blocked.getByRole("button", { name: "Accept" })).toBeDisabled();
    await expect(blocked.getByRole("button", { name: "Reject" })).toHaveAttribute("aria-pressed", "true");
  });
});
