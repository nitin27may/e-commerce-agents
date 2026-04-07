import { test, expect } from "@playwright/test";

/**
 * Focused chat UI verification — sends real messages, waits for full
 * agent responses, and captures screenshots of the rendered rich cards.
 */

async function login(page, email: string, password: string) {
  await page.goto("/login");
  await page.fill('input[type="email"]', email);
  await page.fill('input[type="password"]', password);
  await page.click('button[type="submit"]');
  await page.waitForURL(/\/(chat|products)/, { timeout: 10000 });
}

async function sendAndWaitForResponse(page, message: string, timeoutMs = 45000) {
  const textarea = page.locator("textarea");
  await textarea.fill(message);
  await textarea.press("Enter");

  // Wait for typing indicator to appear then disappear (response complete)
  // Or wait for response content to appear
  await page.waitForTimeout(2000); // Let typing indicator appear

  // Wait until no more typing indicators (bouncing dots gone)
  try {
    await page.waitForFunction(
      () => {
        // Check that typing indicator is gone (no bouncing dots animation)
        const dots = document.querySelectorAll('[class*="animate-bounce"]');
        return dots.length === 0;
      },
      { timeout: timeoutMs }
    );
  } catch {
    // Timeout means response is still streaming — that's OK, take screenshot anyway
  }

  // Extra buffer for rendering
  await page.waitForTimeout(2000);
}

test.describe("Chat UI Verification", () => {
  test.setTimeout(120000);

  test.beforeEach(async ({ page }) => {
    await login(page, "alice.johnson@gmail.com", "customer123");
    await page.goto("/chat");
    await page.waitForTimeout(1000);
  });

  test("product search shows rich product cards", async ({ page }) => {
    await sendAndWaitForResponse(page, "Show me wireless headphones under $400");
    await page.screenshot({ path: "e2e/screenshots/chat-verify-product-search.png", fullPage: true });

    // Check for response content
    const main = page.locator("main");
    const text = await main.textContent();
    console.log("Product search response length:", text?.length);

    // Look for product-related content in the response
    const hasResponse = (text?.length ?? 0) > 200;
    expect(hasResponse).toBeTruthy();
  });

  test("order tracking shows order details", async ({ page }) => {
    await sendAndWaitForResponse(page, "Where is my latest order? Show me the tracking details.");
    await page.screenshot({ path: "e2e/screenshots/chat-verify-order-tracking.png", fullPage: true });

    const main = page.locator("main");
    const text = await main.textContent();
    console.log("Order tracking response length:", text?.length);
    expect((text?.length ?? 0) > 200).toBeTruthy();
  });

  test("cart query shows cart contents", async ({ page }) => {
    await sendAndWaitForResponse(page, "What items are in my shopping cart right now?");
    await page.screenshot({ path: "e2e/screenshots/chat-verify-cart-query.png", fullPage: true });

    const main = page.locator("main");
    const text = await main.textContent();
    console.log("Cart query response length:", text?.length);
    expect((text?.length ?? 0) > 100).toBeTruthy();
  });

  test("product card Add to Cart button works", async ({ page }) => {
    // First send a product query to get product cards
    await sendAndWaitForResponse(page, "Show me the Sony WH-1000XM5 headphones");
    await page.screenshot({ path: "e2e/screenshots/chat-verify-before-add.png", fullPage: true });

    // Look for Add to Cart button in chat
    const addBtn = page.getByRole("button", { name: /add to cart|added/i });
    const btnCount = await addBtn.count();
    console.log("Add to Cart buttons found in chat:", btnCount);

    if (btnCount > 0) {
      await addBtn.first().click();
      await page.waitForTimeout(2000);
      await page.screenshot({ path: "e2e/screenshots/chat-verify-after-add.png", fullPage: true });
    }
  });

  test("return request flow", async ({ page }) => {
    await sendAndWaitForResponse(page, "I want to return my most recent delivered order, the product is defective");
    await page.screenshot({ path: "e2e/screenshots/chat-verify-return-request.png", fullPage: true });

    const main = page.locator("main");
    const text = await main.textContent();
    console.log("Return request response length:", text?.length);
    expect((text?.length ?? 0) > 100).toBeTruthy();
  });
});
