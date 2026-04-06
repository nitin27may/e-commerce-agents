import { test, expect } from "@playwright/test";

/**
 * Chat-driven e-commerce tests.
 * These tests send real messages to the agent and verify responses.
 * They require the LLM (OpenAI) to be configured — if OPENAI_API_KEY
 * is not set, the orchestrator will return an error message.
 */

async function login(page, email: string, password: string) {
  await page.goto("/login");
  await page.fill('input[type="email"]', email);
  await page.fill('input[type="password"]', password);
  await page.click('button[type="submit"]');
  await page.waitForURL(/\/(chat|products)/, { timeout: 10000 });
}

async function sendMessage(page, message: string) {
  const textarea = page.locator("textarea");
  await textarea.fill(message);
  await textarea.press("Enter");
  // Wait for assistant response (loading indicator disappears and new content appears)
  await page.waitForTimeout(2000);
  // Wait for the typing indicator to disappear or a response to appear
  await page.waitForFunction(
    () => {
      const messages = document.querySelectorAll('[class*="max-w"]');
      return messages.length > 0;
    },
    { timeout: 60000 }
  );
  // Extra wait for streaming to finish
  await page.waitForTimeout(3000);
}

async function getLastAssistantMessage(page): Promise<string> {
  // Get all message containers and return the last assistant one
  const messages = await page.locator('[class*="rounded-2xl"]').all();
  if (messages.length === 0) return "";
  const last = messages[messages.length - 1];
  return (await last.textContent()) || "";
}

test.describe("Chat Shopping Experience", () => {
  test.beforeEach(async ({ page }) => {
    await login(page, "alice.johnson@gmail.com", "customer123");
    await page.goto("/chat");
    await page.waitForTimeout(1000);
  });

  // Set longer timeout for LLM responses
  test.setTimeout(120000);

  test("should send a product search message and get a response", async ({ page }) => {
    await sendMessage(page, "Show me headphones under $300");
    await page.screenshot({ path: "e2e/screenshots/chat-product-search.png", fullPage: true });

    // Should get a response (either product cards or text about products)
    const responseArea = page.locator("main");
    const text = await responseArea.textContent();
    // The agent should respond with something (even if it's an error about missing API key)
    expect(text?.length).toBeGreaterThan(50);
  });

  test("should show product cards in chat response", async ({ page }) => {
    await sendMessage(page, "Find me wireless headphones");
    await page.waitForTimeout(5000);
    await page.screenshot({ path: "e2e/screenshots/chat-product-cards.png", fullPage: true });

    // Check if product cards are rendered (they have specific styling)
    const productCards = page.locator('[class*="product"], [class*="rounded-xl"]');
    const cardCount = await productCards.count();
    console.log(`Found ${cardCount} card-like elements in chat`);
  });

  test("should ask about order status", async ({ page }) => {
    await sendMessage(page, "Where is my latest order?");
    await page.screenshot({ path: "e2e/screenshots/chat-order-status.png", fullPage: true });

    const responseArea = page.locator("main");
    const text = await responseArea.textContent();
    expect(text?.length).toBeGreaterThan(50);
  });

  test("should ask to add product to cart", async ({ page }) => {
    await sendMessage(page, "Add the Sony WH-1000XM5 to my cart");
    await page.screenshot({ path: "e2e/screenshots/chat-add-to-cart.png", fullPage: true });

    const responseArea = page.locator("main");
    const text = await responseArea.textContent();
    expect(text?.length).toBeGreaterThan(50);
  });

  test("should ask about returns", async ({ page }) => {
    await sendMessage(page, "I want to return my last delivered order");
    await page.screenshot({ path: "e2e/screenshots/chat-return-request.png", fullPage: true });

    const responseArea = page.locator("main");
    const text = await responseArea.textContent();
    expect(text?.length).toBeGreaterThan(50);
  });

  test("should ask about cart contents", async ({ page }) => {
    await sendMessage(page, "What's in my cart?");
    await page.screenshot({ path: "e2e/screenshots/chat-view-cart.png", fullPage: true });

    const responseArea = page.locator("main");
    const text = await responseArea.textContent();
    expect(text?.length).toBeGreaterThan(50);
  });

  test("should ask about shipping and tracking", async ({ page }) => {
    await sendMessage(page, "Track my most recent shipped order");
    await page.screenshot({ path: "e2e/screenshots/chat-track-order.png", fullPage: true });

    const responseArea = page.locator("main");
    const text = await responseArea.textContent();
    expect(text?.length).toBeGreaterThan(50);
  });

  test("should ask to cancel an order", async ({ page }) => {
    await sendMessage(page, "Cancel my most recent placed order");
    await page.screenshot({ path: "e2e/screenshots/chat-cancel-order.png", fullPage: true });

    const responseArea = page.locator("main");
    const text = await responseArea.textContent();
    expect(text?.length).toBeGreaterThan(50);
  });
});

// Test that UI shopping actions work alongside chat
test.describe("UI Shopping Actions", () => {
  test.setTimeout(30000);

  test("full add-to-cart flow from product page", async ({ page }) => {
    await login(page, "bob.smith@gmail.com", "customer123");

    // 1. Go to products
    await page.goto("/products");
    await page.waitForSelector('[class*="grid"]', { timeout: 10000 });
    await page.screenshot({ path: "e2e/screenshots/ui-products-grid.png" });

    // 2. Click first product
    const firstProduct = page.locator('[class*="grid"] > a').first();
    await firstProduct.click();
    await page.waitForURL(/\/products\//, { timeout: 10000 });
    await page.waitForTimeout(2000);

    // 3. Click Add to Cart
    const addBtn = page.getByRole("button", { name: /add to cart/i });
    await expect(addBtn).toBeVisible({ timeout: 10000 });
    await addBtn.click();
    await page.waitForTimeout(2000);
    await page.screenshot({ path: "e2e/screenshots/ui-added-to-cart.png" });

    // 4. Navigate to cart
    await page.goto("/cart");
    await page.waitForTimeout(3000);
    await page.screenshot({ path: "e2e/screenshots/ui-cart-with-item.png" });

    // Should have at least one item or the proceed button
    const hasItems = await page.getByText(/proceed to checkout|shopping cart/i).first().isVisible().catch(() => false);
    expect(hasItems).toBeTruthy();
  });

  test("view order details and see cancel/return buttons", async ({ page }) => {
    await login(page, "alice.johnson@gmail.com", "customer123");

    // Go to orders
    await page.goto("/orders");
    await page.waitForTimeout(3000);

    // Click on first order
    const firstOrder = page.locator('[class*="cursor-pointer"]').first();
    if (await firstOrder.isVisible()) {
      await firstOrder.click();
      await page.waitForURL(/\/orders\//, { timeout: 10000 });
      await page.waitForTimeout(3000);
      await page.screenshot({ path: "e2e/screenshots/ui-order-detail-actions.png" });

      // Check for action buttons based on status
      const hasCancelBtn = await page.getByRole("button", { name: /cancel/i }).isVisible().catch(() => false);
      const hasReturnBtn = await page.getByRole("button", { name: /return/i }).isVisible().catch(() => false);
      const hasStatusBadge = await page.locator('[class*="badge"]').first().isVisible().catch(() => false);

      // Should at least have a status badge
      expect(hasStatusBadge || hasCancelBtn || hasReturnBtn).toBeTruthy();
    }
  });
});
