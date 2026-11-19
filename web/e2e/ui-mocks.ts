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

const PROPOSALS = {
  proposals: [
    {
      id: "pr-1",
      seller_email: "alice@example.com",
      kind: "mixed",
      trigger: "manual",
      status: "pending_review",
      created_at: "2026-11-16T10:00:00Z",
      updated_at: "2026-11-16T10:00:00Z",
      approval_id: "ap-1",
      approval_status: "pending_review",
      due_at: "2026-11-19T10:00:00Z",
      lines: [
        {
          id: "l-1", product_id: "p1", product_name: "Clean Code", field: "price", current_value: 37.49,
          proposed_value: 33.75, final_value: null, rationale: "410 units on hand, 9 sold in the last 90 days.",
          evidence: {}, guard: { ok: true, reasons: [], numbers: {}, policy_version: "v1" }, status: "proposed", executed_at: null,
        },
        {
          id: "l-2", product_id: "p2", product_name: "Nike Air Max 270", field: "restock_quantity", current_value: 250,
          proposed_value: 24, final_value: null, rationale: "1 warehouse is below its reorder threshold.",
          evidence: {}, guard: { ok: true, reasons: [], numbers: {}, policy_version: "v1" }, status: "proposed", executed_at: null,
        },
        {
          id: "l-3", product_id: "p3", product_name: "Sony Alpha a6700", field: "price", current_value: 1399.99,
          proposed_value: 1260, final_value: null, rationale: "300 units on hand, none sold.",
          evidence: {}, guard: { ok: false, reasons: ["margin_below_floor"], numbers: {}, policy_version: "v1" }, status: "blocked", executed_at: null,
        },
      ],
    },
  ],
  total: 1,
};

const DECISIONS = {
  decisions: [
    {
      id: "d-1", created_at: "2026-11-16T10:00:00Z", event_type: "order.shipment_delayed", action: "goodwill_credit",
      budget_key: "goodwill_credit", customer_email: "carol@example.com", order_id: "12345678-aaaa-bbbb-cccc-1234567890ab",
      amount: "5.00", applied_amount: null, tier: "auto", state: "executed",
      reasons: ["A small credit for a delay of at least two days"],
      budget: { budget_key: "goodwill_credit", day: "2026-11-16", tripped: [], drew: { count: 1, amount: "5.00" } },
      input: { event: { event_id: "e1", delay_days: 3 }, rule: "auto-small-credit" }, policy_version: "v1",
      approval_id: null, approval_status: null, approved_by: null, reversed_at: null, reversed_by: null, can_reverse: true,
    },
    {
      id: "d-2", created_at: "2026-11-16T10:05:00Z", event_type: "order.shipment_delayed", action: "goodwill_credit",
      budget_key: "goodwill_credit", customer_email: "dave@example.com", order_id: "87654321-aaaa-bbbb-cccc-1234567890ab",
      amount: "40.00", applied_amount: null, tier: "needs_approval", state: "awaiting_approval",
      reasons: ["Outside what the system may do alone"],
      budget: { budget_key: "goodwill_credit", day: "2026-11-16", tripped: [], drew: null },
      input: { event: { event_id: "e2", delay_days: 12 }, rule: "default-approval" }, policy_version: "v1",
      approval_id: "ap-2", approval_status: "pending_review", approved_by: null, reversed_at: null, reversed_by: null, can_reverse: false,
    },
  ],
  total: 2,
  budgets: [
    {
      budget_key: "goodwill_credit", day: "2026-11-16", used_count: 1, used_amount: "5.00", max_count: 20, max_amount: "200.00",
      per_action_max_amount: "25.00", per_customer_max_count: 1, per_customer_max_amount: "25.00", per_order_max_count: 1,
    },
  ],
  enabled: true,
  simulator_enabled: true,
  scenarios: { small: 3, medium: 6, large: 12 },
};

const INSPECTIONS = {
  inspections: [
    {
      return_id: "aaaaaaaa-1111-2222-3333-444444444444",
      order_id: "bbbbbbbb-1111-2222-3333-444444444444",
      customer_email: "alice@example.com",
      customer_name: "Alice Johnson",
      reason: "Arrived with a cracked screen",
      refund_amount: 129.95,
      scanned_at: "2026-11-17T09:00:00Z",
      waiting_since: "2026-11-17T09:05:00Z",
      overdue_after_days: 7,
    },
  ],
  total: 1,
};

export async function seedAuth(
  page: Page,
  role: "customer" | "admin" | "seller" = "customer",
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
    if (url.includes("/api/admin/decisions")) return route.fulfill({ json: DECISIONS });
    if (url.includes("/api/seller/proposals")) return route.fulfill({ json: PROPOSALS });
    if (url.includes("/api/staff/returns/inspections")) return route.fulfill({ json: INSPECTIONS });
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
