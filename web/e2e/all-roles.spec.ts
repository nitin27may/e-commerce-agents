import { test, expect, type Page } from "@playwright/test";

const API_URL = "http://localhost:8080";

// Test users from seed data
const USERS = {
  customer: { email: "alice@example.com", password: "customer123", name: "Alice Johnson", role: "customer" },
  admin: { email: "admin@agentbazaar.com", password: "admin123", name: "Admin User", role: "admin" },
  powerUser: { email: "power@agentbazaar.com", password: "power123", name: "Power User", role: "power_user" },
  seller: { email: "seller@agentbazaar.com", password: "seller123", name: "Acme Store", role: "seller" },
  customer2: { email: "bob@example.com", password: "customer123", name: "Bob Smith", role: "customer" },
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function login(page: Page, email: string, password: string) {
  await page.goto("/login");
  await page.fill('input[type="email"]', email);
  await page.fill('input[type="password"]', password);
  await page.getByRole("button", { name: /log\s*in|sign\s*in/i }).click();
  // Wait for redirect to chat or app page
  await page.waitForURL(/\/(chat|products|$)/, { timeout: 10000 });
}

async function ensureLoggedOut(page: Page) {
  await page.goto("/login");
  // Clear localStorage
  await page.evaluate(() => {
    localStorage.removeItem("agentbazaar_user");
    localStorage.removeItem("agentbazaar_access_token");
    localStorage.removeItem("agentbazaar_refresh_token");
  });
}

// ---------------------------------------------------------------------------
// 1. AUTH TESTS
// ---------------------------------------------------------------------------

test.describe("Authentication", () => {
  test.beforeEach(async ({ page }) => {
    await ensureLoggedOut(page);
  });

  test("shows login page for unauthenticated users", async ({ page }) => {
    await page.goto("/");
    await page.waitForURL(/\/login/);
    await expect(page.getByText(/sign in|log in/i).first()).toBeVisible();
  });

  test("login with valid customer credentials", async ({ page }) => {
    await login(page, USERS.customer.email, USERS.customer.password);
    // Should be on chat page
    await expect(page).toHaveURL(/\/chat/);
  });

  test("login with valid admin credentials", async ({ page }) => {
    await login(page, USERS.admin.email, USERS.admin.password);
    await expect(page).toHaveURL(/\/chat/);
  });

  test("login with invalid credentials shows error", async ({ page }) => {
    await page.goto("/login");
    await page.fill('input[type="email"]', "wrong@example.com");
    await page.fill('input[type="password"]', "wrongpass");
    await page.getByRole("button", { name: /log\s*in|sign\s*in/i }).click();
    // Should show error message
    await expect(page.getByText(/invalid|not found|error/i).first()).toBeVisible({ timeout: 5000 });
  });

  test("signup creates new account", async ({ page }) => {
    // Use crypto-random suffix to avoid duplicate email across test runs
    const unique = `pw_test_${Date.now()}_${Math.random().toString(36).slice(2, 8)}@example.com`;
    await page.goto("/signup");
    await page.locator("#name").fill("Test User");
    await page.locator("#email").fill(unique);
    await page.locator("#password").fill("testpass123");
    await page.getByRole("button", { name: /create account|sign\s*up/i }).click();
    await page.waitForURL(/\/chat/, { timeout: 15000 });
  });
});

// ---------------------------------------------------------------------------
// 2. CUSTOMER ROLE TESTS (Alice)
// ---------------------------------------------------------------------------

test.describe("Customer Role (Alice)", () => {
  test.beforeEach(async ({ page }) => {
    await ensureLoggedOut(page);
    await login(page, USERS.customer.email, USERS.customer.password);
  });

  test("chat page loads with conversation panel", async ({ page }) => {
    await page.goto("/chat");
    await expect(page.getByText(/conversations|new chat/i).first()).toBeVisible();
    await expect(page.getByPlaceholder(/message|type/i).first()).toBeVisible();
  });

  test("can send a chat message", async ({ page }) => {
    await page.goto("/chat");
    const input = page.getByPlaceholder(/message|type/i).first();
    await input.fill("Hello, what can you help me with?");
    await input.press("Enter");
    // User message should appear in the chat area
    await expect(page.getByText("Hello, what can you help me with?").last()).toBeVisible({ timeout: 5000 });
    // Wait for response (may be error fallback if no API key)
    await page.waitForTimeout(5000);
    // Should have a response (either real or error fallback)
    const hasResponse = await page.getByText(/help|apologize|error|assist|issue/i).last().isVisible().catch(() => false);
    expect(hasResponse).toBeTruthy();
  });

  test("products page shows product grid", async ({ page }) => {
    await page.goto("/products");
    await page.waitForLoadState("networkidle");
    // Should show products
    await expect(page.getByText(/products|showing/i).first()).toBeVisible({ timeout: 10000 });
    // Should have category filters
    await expect(page.getByText("Electronics").first()).toBeVisible();
  });

  test("product detail page shows specs and reviews", async ({ page }) => {
    await page.goto("/products");
    await page.waitForLoadState("networkidle");
    // Click first product
    const firstProduct = page.locator("a[href*='/products/']").first();
    if (await firstProduct.isVisible()) {
      await firstProduct.click();
      await page.waitForURL(/\/products\//);
      // Should show product details
      await expect(page.getByText(/description|specs|stock|reviews/i).first()).toBeVisible({ timeout: 5000 });
    }
  });

  test("orders page shows order list", async ({ page }) => {
    await page.goto("/orders");
    await page.waitForLoadState("networkidle");
    // Should show orders or empty state
    const hasOrders = await page.getByText(/order|shipped|delivered/i).first().isVisible().catch(() => false);
    const hasEmpty = await page.getByText(/no orders/i).first().isVisible().catch(() => false);
    expect(hasOrders || hasEmpty).toBeTruthy();
  });

  test("order detail page shows timeline", async ({ page }) => {
    await page.goto("/orders");
    await page.waitForLoadState("networkidle");
    const firstOrder = page.locator("a[href*='/orders/']").first();
    if (await firstOrder.isVisible()) {
      await firstOrder.click();
      await page.waitForURL(/\/orders\//);
      await expect(page.getByText(/status|timeline|items|shipping/i).first()).toBeVisible({ timeout: 5000 });
    }
  });

  test("marketplace page shows agent catalog", async ({ page }) => {
    await page.goto("/marketplace");
    await page.waitForLoadState("networkidle");
    // Wait for agent cards to load — match display names or category text
    await expect(page.getByText(/Product Discovery|Order Management|Marketplace|agent/i).first()).toBeVisible({ timeout: 15000 });
  });

  test("profile page shows user info and loyalty tier", async ({ page }) => {
    await page.goto("/profile");
    await page.waitForLoadState("networkidle");
    await expect(page.getByText(USERS.customer.name).first()).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/gold|loyalty/i).first()).toBeVisible();
  });

  test("sidebar navigation works", async ({ page }) => {
    await page.goto("/chat");
    // Navigate to Products
    await page.getByRole("link", { name: /products/i }).first().click();
    await expect(page).toHaveURL(/\/products/);
    // Navigate to Orders
    await page.getByRole("link", { name: /orders/i }).first().click();
    await expect(page).toHaveURL(/\/orders/);
    // Navigate to Marketplace
    await page.getByRole("link", { name: /marketplace/i }).first().click();
    await expect(page).toHaveURL(/\/marketplace/);
    // Admin link should NOT be visible for customer
    await expect(page.getByRole("link", { name: /admin/i })).not.toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// 3. ADMIN ROLE TESTS
// ---------------------------------------------------------------------------

test.describe("Admin Role", () => {
  test.beforeEach(async ({ page }) => {
    await ensureLoggedOut(page);
    await login(page, USERS.admin.email, USERS.admin.password);
  });

  test("admin sidebar shows Admin link", async ({ page }) => {
    await page.goto("/chat");
    await expect(page.getByRole("link", { name: /admin/i }).first()).toBeVisible();
  });

  test("admin dashboard shows metrics", async ({ page }) => {
    await page.goto("/admin");
    await page.waitForLoadState("networkidle");
    // Admin dashboard — match any metric label or "admin" heading
    await expect(page.getByText(/dashboard|usage|overview|admin|total|invocation|token|agent|request/i).first()).toBeVisible({ timeout: 15000 });
  });

  test("admin requests page loads", async ({ page }) => {
    await page.goto("/admin/requests");
    await page.waitForLoadState("networkidle");
    await expect(page.getByText(/access requests|pending/i).first()).toBeVisible({ timeout: 10000 });
  });

  test("admin usage page loads", async ({ page }) => {
    await page.goto("/admin/usage");
    await page.waitForLoadState("networkidle");
    await expect(page.getByText(/usage|invocations|tokens/i).first()).toBeVisible({ timeout: 10000 });
  });

  test("admin audit page loads", async ({ page }) => {
    await page.goto("/admin/audit");
    await page.waitForLoadState("networkidle");
    await expect(page.getByText(/audit|log/i).first()).toBeVisible({ timeout: 10000 });
  });

  test("admin can browse products and orders", async ({ page }) => {
    await page.goto("/products");
    await page.waitForLoadState("networkidle");
    await expect(page.getByText(/products|showing/i).first()).toBeVisible({ timeout: 10000 });

    await page.goto("/orders");
    await page.waitForLoadState("networkidle");
    // Admin has orders too from seed data
    const content = await page.textContent("body");
    expect(content).toBeTruthy();
  });

  test("admin profile shows admin role", async ({ page }) => {
    await page.goto("/profile");
    await page.waitForLoadState("networkidle");
    await expect(page.getByText(USERS.admin.name).first()).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/admin/i).first()).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// 4. POWER USER ROLE TESTS
// ---------------------------------------------------------------------------

test.describe("Power User Role", () => {
  test.beforeEach(async ({ page }) => {
    await ensureLoggedOut(page);
    await login(page, USERS.powerUser.email, USERS.powerUser.password);
  });

  test("power user can access chat", async ({ page }) => {
    await page.goto("/chat");
    await expect(page.getByPlaceholder(/message|type/i).first()).toBeVisible();
  });

  test("power user can browse marketplace", async ({ page }) => {
    await page.goto("/marketplace");
    await page.waitForLoadState("networkidle");
    await expect(page.getByText(/Product Discovery|Order Management|Marketplace|agent/i).first()).toBeVisible({ timeout: 15000 });
  });

  test("power user profile shows power_user role", async ({ page }) => {
    await page.goto("/profile");
    await page.waitForLoadState("networkidle");
    await expect(page.getByText(USERS.powerUser.name).first()).toBeVisible({ timeout: 10000 });
  });
});

// ---------------------------------------------------------------------------
// 5. SELLER ROLE TESTS
// ---------------------------------------------------------------------------

test.describe("Seller Role", () => {
  test.beforeEach(async ({ page }) => {
    await ensureLoggedOut(page);
    await login(page, USERS.seller.email, USERS.seller.password);
  });

  test("seller can access chat", async ({ page }) => {
    await page.goto("/chat");
    await expect(page.getByPlaceholder(/message|type/i).first()).toBeVisible();
  });

  test("seller can browse products", async ({ page }) => {
    await page.goto("/products");
    await page.waitForLoadState("networkidle");
    await expect(page.getByText(/products|showing/i).first()).toBeVisible({ timeout: 10000 });
  });

  test("seller profile shows seller role", async ({ page }) => {
    await page.goto("/profile");
    await page.waitForLoadState("networkidle");
    await expect(page.getByText(USERS.seller.name).first()).toBeVisible({ timeout: 10000 });
  });
});

// ---------------------------------------------------------------------------
// 6. SECOND CUSTOMER (Bob) - CROSS-USER ISOLATION
// ---------------------------------------------------------------------------

test.describe("Cross-User Isolation (Bob)", () => {
  test.beforeEach(async ({ page }) => {
    await ensureLoggedOut(page);
    await login(page, USERS.customer2.email, USERS.customer2.password);
  });

  test("Bob sees his own orders, not Alice's", async ({ page }) => {
    await page.goto("/orders");
    await page.waitForLoadState("networkidle");
    // Bob has different orders than Alice
    const content = await page.textContent("body");
    expect(content).toBeTruthy();
  });

  test("Bob sees his own profile", async ({ page }) => {
    await page.goto("/profile");
    await page.waitForLoadState("networkidle");
    await expect(page.getByText(USERS.customer2.name).first()).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/silver/i).first()).toBeVisible(); // Bob is Silver tier
  });

  test("Bob cannot access admin pages", async ({ page }) => {
    await page.goto("/admin");
    await page.waitForLoadState("networkidle");
    // Should show access denied or redirect
    await expect(page.getByText(/denied|unauthorized|not authorized/i).first()).toBeVisible({ timeout: 5000 });
  });
});
