import { test, expect } from "@playwright/test";
import { seedAuth, mockApi } from "./ui-mocks";

/**
 * Issue #210: "New chat" right after closing the Compare dialog did nothing.
 * Backend-free: every `/api/**` call is stubbed (ui-mocks.ts), so this runs
 * against `pnpm dev` alone. The click goes through `page.mouse` on purpose:
 * `locator.click()` waits for the target to be unobscured, which hides exactly
 * the window a real user's click lands in.
 */
const CONVERSATION = { id: "conv-1", title: "Headphones", updated_at: "2026-11-20T10:00:00Z" };

const CAPS = { streams: true, supports_hitl: false, supports_checkpoints: false, is_graph: false };

test("New chat works immediately after closing the Compare dialog", async ({ page, isMobile }) => {
  test.skip(isMobile, "the desktop conversation list is under test");
  await seedAuth(page);
  await mockApi(page);
  // Registered after mockApi, so these take precedence.
  await page.route("**/api/conversations", (route) => route.fulfill({ json: [CONVERSATION] }));
  await page.route("**/api/conversations/conv-1", (route) =>
    route.fulfill({
      json: {
        ...CONVERSATION,
        messages: [
          { id: "m1", role: "user", content: "Which headphones are best?" },
          { id: "m2", role: "assistant", content: "The Sony WH-1000XM5." },
        ],
      },
    }),
  );
  await page.route("**/api/orchestration/modes", (route) =>
    route.fulfill({
      json: [
        { name: "tool", label: "Tool Router", description: "", capabilities: CAPS, default: true },
        { name: "handoff", label: "Handoff", description: "", capabilities: CAPS, default: false },
      ],
    }),
  );

  await page.goto("/chat");
  await page.getByText("Headphones", { exact: true }).first().click();
  await expect(page.getByText("The Sony WH-1000XM5.")).toBeVisible();

  // Measured before the dialog opens: it aria-hides the rest of the page.
  const box = await page.locator("aside").getByTitle("New chat").boundingBox();
  expect(box).not.toBeNull();

  await page.getByRole("button", { name: /Compare/ }).click();
  await expect(page.getByRole("dialog")).toBeVisible();

  await page.keyboard.press("Escape");
  // No wait for the dialog to detach: a user does not wait either.
  await page.mouse.click(box!.x + box!.width / 2, box!.y + box!.height / 2);

  await expect(page.getByText("How can I help you today?")).toBeVisible();
  await expect(page.getByText("The Sony WH-1000XM5.")).toHaveCount(0);
});
