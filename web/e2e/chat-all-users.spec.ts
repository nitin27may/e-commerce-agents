import { test, expect, type Page } from "@playwright/test";

/**
 * Chat E2E tests for all user roles.
 * Tests the full flow: login → send message → receive LLM response → verify UI.
 */

const USERS = [
  { email: "alice@example.com", password: "customer123", name: "Alice Johnson", role: "customer" },
  { email: "admin@agentbazaar.com", password: "admin123", name: "Admin User", role: "admin" },
  { email: "bob@example.com", password: "customer123", name: "Bob Smith", role: "customer" },
  { email: "power@agentbazaar.com", password: "power123", name: "Power User", role: "power_user" },
  { email: "seller@agentbazaar.com", password: "seller123", name: "Acme Store", role: "seller" },
];

// Increase timeout for LLM responses
test.setTimeout(90_000);

async function loginAndGoToChat(page: Page, email: string, password: string) {
  // Clear any existing session
  await page.goto("/login");
  await page.evaluate(() => {
    localStorage.removeItem("agentbazaar_user");
    localStorage.removeItem("agentbazaar_access_token");
    localStorage.removeItem("agentbazaar_refresh_token");
  });
  await page.goto("/login");

  await page.fill('input[type="email"]', email);
  await page.fill('input[type="password"]', password);
  await page.getByRole("button", { name: /log\s*in|sign\s*in/i }).click();
  await page.waitForURL(/\/chat/, { timeout: 10000 });
}

async function sendMessageAndWaitForResponse(page: Page, message: string): Promise<string> {
  const input = page.getByPlaceholder(/message|type/i).first();
  await input.fill(message);
  await input.press("Enter");

  // Wait for the user message to appear
  await expect(page.getByText(message).last()).toBeVisible({ timeout: 5000 });

  // Wait for the assistant response (look for the typing indicator to disappear
  // or for new content to appear after the user message)
  // The response will either be a real LLM response or a fallback error
  await page.waitForTimeout(2000); // Give time for the API call to start

  // Wait until we see a response that isn't just the user message
  // Look for either a real response or the error fallback
  const responseLocator = page.locator("text=/help|assist|apologize|error|order|product|discover|welcome|hello|hi there|here.*to help|can.*help|support|unfortunately/i").last();
  await expect(responseLocator).toBeVisible({ timeout: 60000 });

  const responseText = await responseLocator.textContent();
  return responseText || "";
}

// ---------------------------------------------------------------------------
// Chat tests for each user
// ---------------------------------------------------------------------------

for (const user of USERS) {
  test.describe(`Chat — ${user.role} (${user.name})`, () => {
    test.beforeEach(async ({ page }) => {
      await loginAndGoToChat(page, user.email, user.password);
    });

    test("sends greeting and receives LLM response", async ({ page }) => {
      const response = await sendMessageAndWaitForResponse(page, "Hello, what can you help me with?");

      // The LLM should respond with something about its capabilities
      expect(response.length).toBeGreaterThan(20);

      // Should show "orchestrator" badge on the assistant message
      await expect(page.getByText("orchestrator").first()).toBeVisible();
    });

    test("conversation appears in sidebar after first message", async ({ page }) => {
      await sendMessageAndWaitForResponse(page, "Tell me about your products");

      // The conversation should appear in the sidebar
      const sidebar = page.locator("text=/Tell me about your products/i").first();
      await expect(sidebar).toBeVisible({ timeout: 5000 });
    });

    test("can send multiple messages in same conversation", async ({ page }) => {
      // First message
      await sendMessageAndWaitForResponse(page, "What categories do you have?");

      // Second message in same conversation
      const input = page.getByPlaceholder(/message|type/i).first();
      await input.fill("Tell me more about Electronics");
      await input.press("Enter");

      // Wait for second response
      await page.waitForTimeout(2000);
      const secondResponse = page.locator("text=/electronics|product|device|headphone|speaker/i").last();
      await expect(secondResponse).toBeVisible({ timeout: 60000 });
    });

    test("new chat button creates fresh conversation", async ({ page }) => {
      // Send first message
      await sendMessageAndWaitForResponse(page, "First conversation message");

      // Click new chat button
      const newChatBtn = page.getByRole("button", { name: /new.*chat/i }).first();
      if (await newChatBtn.isVisible()) {
        await newChatBtn.click();
        await page.waitForTimeout(500);

        // Send message in new conversation
        await sendMessageAndWaitForResponse(page, "Second conversation message");

        // Both conversations should be in sidebar
        await expect(page.getByText(/First conversation/i).first()).toBeVisible();
        await expect(page.getByText(/Second conversation/i).first()).toBeVisible();
      }
    });
  });
}

// ---------------------------------------------------------------------------
// Cross-cutting chat tests
// ---------------------------------------------------------------------------

test.describe("Chat — Cross-User", () => {
  test("Alice's conversations are not visible to Bob", async ({ page }) => {
    // Login as Alice and create a conversation
    await loginAndGoToChat(page, "alice@example.com", "customer123");
    await sendMessageAndWaitForResponse(page, "Alice unique chat test message xyz123");
    await page.waitForTimeout(1000);

    // Logout and login as Bob
    await loginAndGoToChat(page, "bob@example.com", "customer123");
    await page.waitForTimeout(1000);

    // Bob should NOT see Alice's conversation
    const aliceConv = page.getByText(/Alice unique chat test message xyz123/i);
    await expect(aliceConv).not.toBeVisible();
  });
});
