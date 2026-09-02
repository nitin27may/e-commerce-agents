import { test, expect, type Page } from "@playwright/test";

/**
 * Generative-UI regression coverage for the 3 specialists wired in
 * Phase 8.4 Stage 4 (review-sentiment, inventory-fulfillment,
 * pricing-promotions) — the still-pending part of 8.3, using real
 * `expect()`s scoped to actual rendered DOM (table headers, chart axes,
 * status-badge text) instead of a bare response-length check.
 *
 * Requires the LLM (Azure OpenAI / OpenAI) to be configured — if not,
 * the orchestrator returns an error message and these assertions fail
 * loudly rather than silently passing on an error string.
 */

async function login(page: Page, email: string, password: string) {
  await page.goto("/login");
  await page.fill('input[type="email"]', email);
  await page.fill('input[type="password"]', password);
  await page.click('button[type="submit"]');
  await page.waitForURL(/\/(chat|products)/, { timeout: 10000 });
}

async function sendMessageAndWaitForTurn(page: Page, message: string) {
  const textarea = page.locator("textarea");
  await textarea.fill(message);
  await textarea.press("Enter");
  // The composer's submit button shows a red stop icon while the turn is
  // in flight and swaps back to the send icon once isResponding flips
  // false — the reliable "turn actually finished" signal for this app.
  // "Routing to specialists..." only means streaming *started*; waiting
  // on it instead lets a follow-up action fire before the turn is really
  // done (found live during Phase 8.4 Stage 5 — see the memory note
  // ecommerce-agents-playwright-streaming-wait.md).
  const stopButton = page.locator("button.bg-red-500");
  await stopButton.waitFor({ state: "visible", timeout: 10000 }).catch(() => {});
  await stopButton.waitFor({ state: "hidden", timeout: 90000 });
}

test.describe("Generative UI — review-sentiment, inventory-fulfillment, pricing-promotions", () => {
  test.setTimeout(120000);

  test("review-sentiment renders a distribution chart and trend chart, never raw JSON", async ({ page }) => {
    await login(page, "carol.davis@gmail.com", "customer123");
    await page.goto("/chat");
    await sendMessageAndWaitForTurn(page, "Find the Sony WH-1000XM5 headphones");
    await sendMessageAndWaitForTurn(
      page,
      "What's the sentiment on its reviews? Show me the rating breakdown and how it's trended over the last 6 months."
    );

    const main = page.locator("main");
    const text = await main.textContent();
    expect(text).not.toContain("```sentiment");
    expect(text).not.toContain("overall_sentiment");

    // All three scoped with .first(), for the reason spelled out in the
    // warehouse test below: a two-turn conversation legitimately renders more
    // than one card, and asserting a unique match fails the test for the agent
    // doing *more* of its job. The claim is that the chart rendered, not that
    // exactly one did. Observed failing both through the /api proxy and
    // directly against the orchestrator, so it is the spec, not the transport.
    // Rating-distribution bar chart's x-axis labels (DistributionChart)
    await expect(page.getByText("5★", { exact: true }).first()).toBeVisible();
    // Trend chart's section label (TrendChart)
    await expect(page.getByText("Rating over time").first()).toBeVisible();
    // Pros/cons lists
    await expect(page.getByText("Pros", { exact: true }).first()).toBeVisible();
  });

  test("inventory-fulfillment renders a real warehouse table with column headers", async ({ page }) => {
    await login(page, "dave.wilson@gmail.com", "customer123");
    await page.goto("/chat");
    await sendMessageAndWaitForTurn(page, "Find the Sony WH-1000XM5 headphones");
    await sendMessageAndWaitForTurn(page, "Is it in stock? Show me the breakdown by warehouse.");

    const main = page.locator("main");
    const text = await main.textContent();
    expect(text).not.toContain("```inventory");
    expect(text).not.toContain("total_quantity");

    // DataTable's real <th> column headers, not just prose mentioning them.
    // Scoped with .first() deliberately: a stock question often also pulls the
    // restock schedule, which renders a second, legitimate table whose first
    // column is likewise "Warehouse". Asserting a unique match would make this
    // test fail for the agent doing *more* of its job, which is the wrong
    // signal — the claim here is that a real DataTable rendered, not that
    // exactly one did. "Region" was assumed unique to the stock table and to
    // keep the assertion specific. It is not: on a live run it resolved to two
    // column headers, so the restock table carries it too. Same reasoning,
    // same fix. Verified against both the /api proxy and a frontend calling
    // the orchestrator directly, so this is the spec, not the transport.
    await expect(page.getByRole("columnheader", { name: "Warehouse" }).first()).toBeVisible();
    await expect(page.getByRole("columnheader", { name: "Region" }).first()).toBeVisible();
    // The warehouse-breakdown section label, which the card renders whenever
    // it has warehouse rows — the thing this test actually asks for.
    //
    // Two nearby assertions were tried and rejected, both because they test
    // model whim rather than the app. The In Stock / Out of Stock StatusBadge
    // needs an `in_stock` field, and this question is answerable by either of
    // two registered tools: `check_stock` returns `in_stock`, while
    // `get_warehouse_availability` — an equally correct pick, and the one that
    // supplies the restock table — does not, on *either* stack. And the card
    // heading is `product_name || "Stock & Fulfillment"`, so asserting either
    // string fails whenever the model omits or includes that optional field.
    // Both were observed failing on a different backend each.
    // Exact match: the user's own turn ("…breakdown by warehouse") and the
    // conversation title in the sidebar both contain the phrase otherwise.
    await expect(page.getByText("By warehouse", { exact: true }).first()).toBeVisible();
  });

  test("pricing-promotions renders a real deals table with column headers", async ({ page }) => {
    await login(page, "alice.johnson@gmail.com", "customer123");
    await page.goto("/chat");
    await sendMessageAndWaitForTurn(page, "What deals and promotions are currently active on the platform?");

    const main = page.locator("main");
    const text = await main.textContent();
    expect(text).not.toContain("```pricing");
    expect(text).not.toContain("discount_value");

    // .first() for the same reason as the warehouse table above: "what deals
    // are active" is answerable with one deals table or several.
    await expect(page.getByRole("columnheader", { name: "Code" }).first()).toBeVisible();
    await expect(page.getByRole("columnheader", { name: "Discount" }).first()).toBeVisible();
  });

  test("inventory-fulfillment's shipping-options picker is real and clickable", async ({ page }) => {
    await login(page, "emma.brown@gmail.com", "customer123");
    await page.goto("/chat");
    await sendMessageAndWaitForTurn(page, "Find the Sony WH-1000XM5 headphones");
    await sendMessageAndWaitForTurn(
      page,
      "How much would it cost to ship this to the east region, and what are my carrier options?"
    );

    const selectButtons = page.getByRole("button", { name: "Select" });
    // Longer than the default 10 s on purpose. The turn is complete by the time
    // sendMessageAndWaitForTurn returns, but the shipping card is rendered from
    // the fenced payload in the final message — under full-suite load that last
    // render lands after the composer has already re-enabled. This test passes
    // in isolation and failed only in a full run, which is the signature of a
    // wait that is too tight rather than a card that never arrives.
    await expect(selectButtons.first()).toBeVisible({ timeout: 30000 });
    const countBefore = await page.locator("textarea").count(); // sanity: composer present
    expect(countBefore).toBeGreaterThan(0);

    await selectButtons.first().click();
    // A real user-authored confirmation bubble must appear — the click
    // must not silently no-op (see the timing note above; this test is
    // itself the regression guard for that exact class of bug).
    await expect(page.getByText(/I'll go with/)).toBeVisible({ timeout: 10000 });
  });
});
