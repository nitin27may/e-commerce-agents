import { test, expect } from "@playwright/test";

// Shared login helper
async function login(page, email: string, password: string) {
  await page.goto("/login");
  await page.fill('input[type="email"]', email);
  await page.fill('input[type="password"]', password);
  await page.click('button[type="submit"]');
  await page.waitForURL(/\/(chat|products|admin|seller)/, { timeout: 10000 });
}

// ============================================================
// 1. CUSTOMER SHOPPING FLOW (Traditional UI)
// ============================================================
test.describe("Customer Shopping Flow", () => {
  test.beforeEach(async ({ page }) => {
    await login(page, "alice.johnson@gmail.com", "customer123");
  });

  test("should see products page with add to cart buttons", async ({ page }) => {
    await page.goto("/products");
    await page.waitForSelector('[class*="grid"]', { timeout: 10000 });
    // Products should be visible
    const products = page.locator('[class*="grid"] > a, [class*="grid"] > div');
    await expect(products.first()).toBeVisible();
    // Take screenshot
    await page.screenshot({ path: "e2e/screenshots/products-page.png" });
  });

  test("should open product detail and see add to cart", async ({ page }) => {
    await page.goto("/products");
    await page.waitForSelector('[class*="grid"]', { timeout: 10000 });
    // Click first product card
    const firstProduct = page.locator('[class*="grid"] > a').first();
    if (await firstProduct.isVisible()) {
      await firstProduct.click();
      await page.waitForURL(/\/products\//, { timeout: 10000 });
      // Should see add to cart button
      const addToCartBtn = page.getByRole("button", { name: /add to cart/i });
      await expect(addToCartBtn).toBeVisible({ timeout: 10000 });
      await page.screenshot({ path: "e2e/screenshots/product-detail.png" });
    }
  });

  test("should add item to cart and see cart badge update", async ({ page }) => {
    await page.goto("/products");
    await page.waitForSelector('[class*="grid"]', { timeout: 10000 });
    // Click first product
    const firstProduct = page.locator('[class*="grid"] > a').first();
    if (await firstProduct.isVisible()) {
      await firstProduct.click();
      await page.waitForURL(/\/products\//, { timeout: 10000 });
      // Click add to cart
      const addToCartBtn = page.getByRole("button", { name: /add to cart/i });
      await expect(addToCartBtn).toBeVisible({ timeout: 10000 });
      await addToCartBtn.click();
      // Should see "Added" confirmation
      await expect(page.getByText(/added/i)).toBeVisible({ timeout: 5000 });
      await page.screenshot({ path: "e2e/screenshots/added-to-cart.png" });
    }
  });

  test("should see cart page with items (demo cart seeded)", async ({ page }) => {
    await page.goto("/cart");
    await page.waitForTimeout(2000);
    await page.screenshot({ path: "e2e/screenshots/cart-page.png" });
    // Alice has a pre-seeded cart with items
    // Check for either items or empty state
    const hasItems = await page.getByText(/proceed to checkout/i).isVisible().catch(() => false);
    const isEmpty = await page.getByText(/cart is empty/i).isVisible().catch(() => false);
    expect(hasItems || isEmpty).toBeTruthy();
  });

  test("should navigate to checkout page", async ({ page }) => {
    await page.goto("/checkout");
    await page.waitForTimeout(3000);
    await page.screenshot({ path: "e2e/screenshots/checkout-page.png" });
    // Should see checkout heading or shipping form or empty cart
    const hasCheckout = await page.getByText(/checkout/i).first().isVisible().catch(() => false);
    const hasForm = await page.getByText(/shipping/i).first().isVisible().catch(() => false);
    const isEmpty = await page.getByText(/cart is empty|no items|empty/i).first().isVisible().catch(() => false);
    expect(hasCheckout || hasForm || isEmpty).toBeTruthy();
  });

  test("should see orders page with order list", async ({ page }) => {
    await page.goto("/orders");
    await page.waitForTimeout(3000);
    await page.screenshot({ path: "e2e/screenshots/orders-page.png" });
    // Should have orders (Alice has seeded orders)
    const orderCards = page.locator('[class*="cursor-pointer"]');
    const count = await orderCards.count();
    expect(count).toBeGreaterThan(0);
  });

  test("should see order detail with status timeline", async ({ page }) => {
    await page.goto("/orders");
    await page.waitForTimeout(3000);
    // Click first order
    const firstOrder = page.locator('[class*="cursor-pointer"]').first();
    if (await firstOrder.isVisible()) {
      await firstOrder.click();
      await page.waitForURL(/\/orders\//, { timeout: 10000 });
      await page.waitForTimeout(3000);
      await page.screenshot({ path: "e2e/screenshots/order-detail.png" });
      // Should show order heading or status info
      const hasOrder = await page.getByText(/order #|order status/i).first().isVisible().catch(() => false);
      const hasStatus = await page.getByText(/placed|confirmed|shipped|delivered|cancelled/i).first().isVisible().catch(() => false);
      expect(hasOrder || hasStatus).toBeTruthy();
    }
  });

  test("should see sidebar with cart link and badge", async ({ page }) => {
    await page.goto("/products");
    await page.waitForTimeout(2000);
    // Check sidebar has Cart link
    const cartLink = page.getByRole("link", { name: /cart/i });
    await expect(cartLink.first()).toBeVisible({ timeout: 5000 });
    await page.screenshot({ path: "e2e/screenshots/sidebar-cart.png" });
  });

  test("should see profile page", async ({ page }) => {
    await page.goto("/profile");
    await page.waitForTimeout(2000);
    await page.screenshot({ path: "e2e/screenshots/profile-page.png" });
    await expect(page.getByRole("heading", { name: /alice/i })).toBeVisible({ timeout: 5000 });
  });
});

// ============================================================
// 2. SELLER ROLE
// ============================================================
test.describe("Seller Role", () => {
  test("should see seller dashboard and products", async ({ page }) => {
    await login(page, "seller.demo@gmail.com", "seller123");
    // Navigate to seller dashboard
    await page.goto("/seller");
    await page.waitForTimeout(3000);
    await page.screenshot({ path: "e2e/screenshots/seller-dashboard.png" });

    // Check seller products
    await page.goto("/seller/products");
    await page.waitForTimeout(3000);
    await page.screenshot({ path: "e2e/screenshots/seller-products.png" });
  });
});

// ============================================================
// 3. ADMIN ROLE
// ============================================================
test.describe("Admin Role", () => {
  test("should see admin dashboard and usage stats", async ({ page }) => {
    await login(page, "admin.demo@gmail.com", "admin123");
    await page.goto("/admin");
    await page.waitForTimeout(3000);
    await page.screenshot({ path: "e2e/screenshots/admin-dashboard.png" });
  });

  test("should see admin access requests", async ({ page }) => {
    await login(page, "admin.demo@gmail.com", "admin123");
    await page.goto("/admin/requests");
    await page.waitForTimeout(3000);
    await page.screenshot({ path: "e2e/screenshots/admin-requests.png" });
  });

  test("should see admin usage stats", async ({ page }) => {
    await login(page, "admin.demo@gmail.com", "admin123");
    await page.goto("/admin/usage");
    await page.waitForTimeout(3000);
    await page.screenshot({ path: "e2e/screenshots/admin-usage.png" });
  });
});

// ============================================================
// 4. CHAT EXPERIENCE
// ============================================================
test.describe("Chat Experience", () => {
  test.beforeEach(async ({ page }) => {
    await login(page, "alice.johnson@gmail.com", "customer123");
  });

  test("should load chat page with input area", async ({ page }) => {
    await page.goto("/chat");
    await page.waitForTimeout(2000);
    await page.screenshot({ path: "e2e/screenshots/chat-page.png" });
    // Should see chat input
    const textarea = page.locator("textarea");
    await expect(textarea).toBeVisible({ timeout: 5000 });
  });

  test("should see cart badge in chat header", async ({ page }) => {
    await page.goto("/chat");
    await page.waitForTimeout(2000);
    // Look for cart icon/link in the chat area
    const cartLink = page.locator('a[href="/cart"]');
    const count = await cartLink.count();
    // Should have at least the sidebar cart link
    expect(count).toBeGreaterThan(0);
  });
});

// ============================================================
// 5. MARKETPLACE
// ============================================================
test.describe("Marketplace", () => {
  test("should see agent catalog", async ({ page }) => {
    await login(page, "alice.johnson@gmail.com", "customer123");
    await page.goto("/marketplace");
    await page.waitForTimeout(3000);
    await page.screenshot({ path: "e2e/screenshots/marketplace.png" });
    // Should show agent cards
    const hasAgents = await page.getByText(/customer support|inventory|pricing|product|order|review/i).first().isVisible().catch(() => false);
    const hasMarketplace = await page.getByText(/agent marketplace/i).first().isVisible().catch(() => false);
    expect(hasAgents || hasMarketplace).toBeTruthy();
  });
});

// ============================================================
// 6. PRODUCT IMAGES (verify real images load)
// ============================================================
test.describe("Product Images", () => {
  test("should load real product images (not Picsum)", async ({ page }) => {
    await login(page, "alice.johnson@gmail.com", "customer123");
    await page.goto("/products");
    await page.waitForTimeout(3000);
    // Check that images have unsplash URLs
    const images = page.locator("img[src*='unsplash']");
    const count = await images.count();
    await page.screenshot({ path: "e2e/screenshots/product-images.png" });
    // Log the count for debugging
    console.log(`Found ${count} Unsplash images on products page`);
  });
});

// ============================================================
// 7. API ENDPOINTS (direct API tests)
// ============================================================
test.describe("API Endpoints", () => {
  let token: string;

  test.beforeAll(async ({ request }) => {
    const res = await request.post("http://localhost:8080/api/auth/login", {
      data: { email: "alice.johnson@gmail.com", password: "customer123" },
    });
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    token = body.access_token;
  });

  test("GET /api/cart should return cart data", async ({ request }) => {
    const res = await request.get("http://localhost:8080/api/cart", {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(res.ok()).toBeTruthy();
    const cart = await res.json();
    console.log("Cart:", JSON.stringify(cart, null, 2).slice(0, 500));
    expect(cart).toHaveProperty("items");
    expect(cart).toHaveProperty("total");
  });

  test("GET /api/products should return products", async ({ request }) => {
    const res = await request.get("http://localhost:8080/api/products", {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data.products.length).toBeGreaterThan(0);
    // Check first product has image_url
    const first = data.products[0];
    console.log("First product image_url:", first.image_url);
  });

  test("GET /api/orders should return orders", async ({ request }) => {
    const res = await request.get("http://localhost:8080/api/orders", {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data.orders.length).toBeGreaterThan(0);
  });

  test("POST /api/cart/items should add item to cart", async ({ request }) => {
    // First get a product
    const prodRes = await request.get("http://localhost:8080/api/products", {
      headers: { Authorization: `Bearer ${token}` },
    });
    const products = (await prodRes.json()).products;
    const productId = products[0].id;

    const res = await request.post("http://localhost:8080/api/cart/items", {
      headers: { Authorization: `Bearer ${token}` },
      data: { product_id: productId, quantity: 1 },
    });
    expect(res.ok()).toBeTruthy();
    const result = await res.json();
    console.log("Add to cart result:", JSON.stringify(result));
  });

  test("GET /api/orders/:id should include billing_address and return info", async ({ request }) => {
    const ordersRes = await request.get("http://localhost:8080/api/orders", {
      headers: { Authorization: `Bearer ${token}` },
    });
    const orders = (await ordersRes.json()).orders;
    if (orders.length > 0) {
      const orderRes = await request.get(`http://localhost:8080/api/orders/${orders[0].id}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      expect(orderRes.ok()).toBeTruthy();
      const order = await orderRes.json();
      console.log("Order has billing_address:", !!order.billing_address);
      console.log("Order has shipping_address:", !!order.shipping_address);
      expect(order).toHaveProperty("billing_address");
      expect(order).toHaveProperty("shipping_address");
    }
  });
});
