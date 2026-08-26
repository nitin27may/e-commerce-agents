import { test, expect, type Page } from "@playwright/test";

/**
 * UI smoke suite for the enhanced shell + Phase 2 surfaces.
 *
 * Auth and the backend API are mocked (localStorage session + request
 * interception) so this runs against the frontend alone — no live stack.
 * Like the rest of e2e/, it runs locally against `pnpm dev`, not in CI.
 */

const IMG =
  "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='400' height='300'%3E%3Crect width='400' height='300' fill='%2393c5cf'/%3E%3C/svg%3E";

const USAGE = {
  overall: {
    total_requests: 12840,
    total_tokens_in: 4230000,
    total_tokens_out: 1180000,
    avg_duration_ms: 1240,
    pending_requests: 2,
  },
  by_agent: [
    { agent_name: "orchestrator", request_count: 5200, tokens_in: 1800000, tokens_out: 520000, avg_duration_ms: 1450 },
    { agent_name: "product_discovery", request_count: 3100, tokens_in: 980000, tokens_out: 240000, avg_duration_ms: 980 },
  ],
  daily: [
    { date: "2026-05-27", request_count: 1890, tokens_in: 680000, tokens_out: 188000 },
    { date: "2026-05-28", request_count: 2360, tokens_in: 820000, tokens_out: 230000 },
    { date: "2026-05-29", request_count: 2410, tokens_in: 500000, tokens_out: 152000 },
  ],
};

const ORDERS = {
  orders: [
    { id: "a1b2c3d4e5", status: "delivered", total: 329.98, created_at: "2026-05-20" },
    { id: "f6g7h8i9j0", status: "shipped", total: 89.5, created_at: "2026-05-26" },
  ],
};

const PRODUCTS = {
  products: [
    { id: "p1", name: "Sony WH-1000XM5", price: 299.99, brand: "Sony", category: "Electronics", image_url: IMG },
    { id: "p2", name: "Ember Smart Mug 2", price: 129.95, brand: "Ember", category: "Home", image_url: IMG },
  ],
  total: 2,
  categories: ["Electronics", "Home"],
};

async function seedAuth(
  page: Page,
  role: "customer" | "admin" = "customer",
  theme: "light" | "dark" = "light",
) {
  await page.addInitScript(
    ([r, t]) => {
      localStorage.setItem(
        "ecommerce_user",
        JSON.stringify({ name: "Alice Johnson", email: "alice@example.com", role: r }),
      );
      localStorage.setItem("ecommerce_access_token", "mock.jwt");
      localStorage.setItem("ecommerce_refresh_token", "mock.refresh");
      localStorage.setItem("theme", t as string);
    },
    [role, theme] as const,
  );
}

async function mockApi(page: Page) {
  await page.route("**/api/**", async (route) => {
    const url = route.request().url();
    if (url.includes("/api/admin/usage")) return route.fulfill({ json: USAGE });
    if (url.includes("/api/orders")) return route.fulfill({ json: ORDERS });
    if (url.includes("/api/products")) return route.fulfill({ json: PRODUCTS });
    if (url.includes("/api/cart"))
      return route.fulfill({ json: { items: [], item_count: 2, subtotal: 174.9 } });
    if (url.includes("/api/conversations")) return route.fulfill({ json: [] });
    // The chat page reads the mode registry on mount. Returning {} here sent it
    // to an error boundary ("This page couldn't load"), which is why the admin
    // sidebar assertions failed against a page that never rendered — nothing to
    // do with the backend under test.
    if (url.includes("/api/orchestration/modes"))
      return route.fulfill({ json: { modes: [{ name: "tool", label: "Tool Router", description: "", is_graph: false }] } });
    // Anything unmatched gets an empty ARRAY rather than an empty object: most
    // of this app's endpoints return collections, and {} is the shape most
    // likely to throw in a .map().
    return route.fulfill({ json: [] });
  });
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

  test("grouped sidebar shows admin nav (Usage, Audit) for admins", async ({ page }) => {
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
    await expect(page.getByRole("link", { name: "Usage" })).toBeVisible();
    // No "Audit": /admin/audit duplicated /runs and its nav entry was removed
    // deliberately. src/lib/nav.test.ts asserts its absence, so expecting it
    // here made the two suites contradict each other — this e2e test simply
    // never caught up. The page itself still exists; only the link went.
    await expect(page.getByRole("link", { name: "Audit" })).toHaveCount(0);
    // The agent marketplace was removed entirely.
    await expect(page.getByRole("link", { name: "Marketplace" })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Requests" })).toHaveCount(0);
  });

  test("buyers see only shop + account nav", async ({ page }) => {
    await seedAuth(page, "customer");
    await mockApi(page);
    await page.goto("/home");
    // Scope to the sidebar — "Chat"/"Profile" also appear as the home "Open chat"
    // quick-prompt and the top-bar avatar (aria-label="Profile").
    const sidebar = page.getByRole("complementary");
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
});
