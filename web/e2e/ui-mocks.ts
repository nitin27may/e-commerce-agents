import type { Page } from "@playwright/test";

/**
 * Shared fixtures for the backend-free specs (ui-smoke, a11y): a seeded
 * localStorage session plus a stub for every `/api/**` call. Not a spec file,
 * so Playwright does not collect it.
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

export async function seedAuth(
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

export async function mockApi(page: Page) {
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
