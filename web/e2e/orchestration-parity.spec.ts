import { test, expect, type Page } from "@playwright/test";
import { BACKEND, skipIfKnownGap } from "./parity-gaps";

/**
 * The parity gate: the orchestration-showcase surfaces, asserted the same way
 * against both backends.
 *
 * These are deliberately the features the rest of `web/e2e/` never touches.
 * That suite is 109 tests across 9 specs and references the backend-sensitive
 * surfaces exactly once in total, so it passes green against a .NET backend
 * with four features missing. Everything here checks for *presence* — the
 * failures being guarded against are silent ones:
 *
 *   - the mode switcher returns `null` when `GET /modes` 404s, so it vanishes
 *     rather than erroring
 *   - `/runs` swallows a 404 in `.catch(() => {})`, so the page looks healthy
 *     with permanently empty panels
 *   - the profile's "AI Memory" card says "chat with the product or review
 *     agents to build your profile", which on .NET can never work
 *   - `mode` is dropped by a backend whose request record has no such field,
 *     so a user picks a workflow and silently gets tool mode
 *
 * A test asserting "no error appeared" would pass against every one of those.
 */

const CUSTOMER = { email: "alice.johnson@gmail.com", password: "customer123" };

async function login(page: Page) {
  await page.goto("/login");
  await page.fill('input[type="email"]', CUSTOMER.email);
  await page.fill('input[type="password"]', CUSTOMER.password);
  await page.click('button[type="submit"]');
  await page.waitForURL(/\/(chat|products)/, { timeout: 15000 });
}

/**
 * Waits for a chat turn to finish. The composer's submit button shows a stop
 * icon while `isResponding` is true and swaps back when the turn completes —
 * the only reliable signal. "Routing to specialists..." means streaming
 * *started*, and acting on it races the guard in `sendMessage`.
 */
async function sendMessageAndWaitForTurn(page: Page, message: string) {
  const textarea = page.locator("textarea");
  await textarea.fill(message);
  await textarea.press("Enter");
  await page.waitForSelector('button[aria-label="Stop"], button:has(svg.lucide-square)', {
    timeout: 20000,
    state: "attached",
  }).catch(() => {});
  await page.waitForSelector('button[aria-label="Stop"], button:has(svg.lucide-square)', {
    timeout: 120000,
    state: "detached",
  });
}

test.describe(`orchestration parity [${BACKEND}]`, () => {
  // Serial because several of these drive a real multi-agent turn and the
  // later ones depend on state the earlier ones create. The timeout is well
  // above the repo default: a workflow-mode turn fans out to several
  // specialists and each makes real LLM calls, so 30s caps it mid-run and the
  // failure looks like a missing element rather than a slow one.
  test.describe.configure({ mode: "serial", timeout: 180_000 });

  test("mode switcher offers more than one orchestration mode", async ({ page }) => {
    skipIfKnownGap("mode switcher offers more than one orchestration mode");
    await login(page);
    await page.goto("/chat");

    // The switcher renders only once GET /api/orchestration/modes resolves
    // with a non-empty list, so its presence *is* the assertion.
    const switcher = page.locator('[aria-label="Orchestration mode"]').first();
    await expect(switcher, "the mode switcher must render, not silently return null").toBeVisible({
      timeout: 15000,
    });

    await switcher.click();

    // base-ui renders select items as plain divs with no role or data-slot,
    // so they are only addressable by text inside the popup.
    const menu = page.locator('[data-slot="select-content"]');
    await expect(menu, "the mode list must open").toBeVisible({ timeout: 10000 });
    await expect(menu.getByText("Tool Router", { exact: false })).toBeVisible();
    await expect(
      menu.getByText(/Pre-Purchase|Handoff|Group Chat/i).first(),
      "more than the default tool router must be offered",
    ).toBeVisible();
  });

  test("selecting a workflow mode is honoured, not silently downgraded", async ({ page }) => {
    skipIfKnownGap("selecting a workflow mode is honoured, not silently downgraded");
    await login(page);
    await page.goto("/chat");

    const switcher = page.locator('[aria-label="Orchestration mode"]').first();
    await switcher.click();
    await page.locator('[data-slot="select-content"]').getByText(/Pre-Purchase/i).first().click();

    await sendMessageAndWaitForTurn(page, "Should I buy the Sony WH-1000XM5?");

    // The UI surfaces the executed mode by rendering OrchestrationGraph, which
    // is gated on msg.mode being set. Asserting on a node label unique to the
    // pre-purchase graph is what separates "honoured" from "silently
    // downgraded" — a backend that drops the field still answers normally in
    // tool mode, which otherwise looks correct.
    const graph = page.locator('svg[id^="orchestration-graph"]').first();
    await expect(graph, "a graph must render for a workflow mode").toBeVisible({ timeout: 20000 });
    await expect(
      graph.getByText(/reviews|stock|price|synthes/i).first(),
      "the graph must be the pre-purchase graph, not the tool router's",
    ).toBeVisible({ timeout: 10000 });
  });

  test("the orchestration graph renders for a workflow mode", async ({ page }) => {
    skipIfKnownGap("the orchestration graph renders for a workflow mode");
    await login(page);
    await page.goto("/chat");

    const switcher = page.locator('[aria-label="Orchestration mode"]').first();
    await switcher.click();
    await page.locator('[data-slot="select-content"]').getByText(/Pre-Purchase/i).first().click();

    await sendMessageAndWaitForTurn(page, "Should I buy the Dyson V15?");

    // Mermaid renders to an inline SVG the component ids "orchestration-graph-N".
    // An empty container means the graph endpoint 404'd and setSource(null) ran.
    const graph = page.locator('svg[id^="orchestration-graph"]').first();
    await expect(graph, "the graph must render as SVG, not an empty panel").toBeVisible({
      timeout: 20000,
    });
  });

  test("mode comparison can be run with two modes selected", async ({ page }) => {
    skipIfKnownGap("mode comparison can be run with two modes selected");
    await login(page);
    await page.goto("/chat");

    await page.getByRole("button", { name: /^Compare$/i }).first().click();

    const dialog = page.locator('[role="dialog"]');
    await expect(dialog).toBeVisible({ timeout: 15000 });

    // The dialog is populated from GET /api/orchestration/modes. On a backend
    // without it the dialog still opens but lists nothing, and the run button
    // stays permanently disabled behind its "pick at least 2 modes" guard —
    // so listing the modes is the assertion that matters.
    await expect(dialog.getByText("Tool Router", { exact: false })).toBeVisible();
    await expect(dialog.getByText(/Pre-Purchase|Handoff|Group Chat/i).first()).toBeVisible();

    // Deliberately stops at "the modes are listed" rather than driving the
    // selection mechanics. This gate exists to prove the *backend* supplies
    // what the UI needs; how the dialog toggles a selection is the UI's own
    // concern and is already covered by the existing suite. Asserting UI
    // internals here would make the gate fail for reasons that have nothing
    // to do with parity.
    await expect(
      dialog.getByRole("button", { name: /Run comparison/i }),
      "the run control must be present once modes are available",
    ).toBeVisible();
  });

  test("a run's checkpoints are listed on /runs", async ({ page }) => {
    skipIfKnownGap("a run's checkpoints are listed on /runs");
    await login(page);
    await page.goto("/chat");

    const switcher = page.locator('[aria-label="Orchestration mode"]').first();
    await switcher.click();
    await page.locator('[data-slot="select-content"]').getByText(/Return & Replace/i).first().click();
    await sendMessageAndWaitForTurn(page, "I want to return my most recent order");

    await page.goto("/runs");
    const row = page.getByText("I want to return my most recent order").first();
    await expect(row, "the run must appear in the list").toBeVisible({ timeout: 20000 });

    // Asserted at the API for the same reason as the memories test: /runs
    // absorbs a 404 in `.catch(() => {})`, so the page renders perfectly with
    // permanently empty panels. From the DOM alone, "no checkpoints endpoint"
    // and "this run has no checkpoints" look identical — and that
    // indistinguishability is the silent failure being guarded against.
    const token = await page.evaluate(() =>
      localStorage.getItem("ecommerce_access_token") ?? "",
    );
    const runs = await page.request.get("http://localhost:8080/api/runs", {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(runs.status()).toBe(200);

    const body = await runs.json();
    const runId = body.entries?.[0]?.id;
    expect(runId, "there must be at least one run to inspect").toBeTruthy();

    const checkpoints = await page.request.get(
      `http://localhost:8080/api/runs/${runId}/checkpoints`,
      { headers: { Authorization: `Bearer ${token}` } },
    );
    expect(
      checkpoints.status(),
      "the checkpoints endpoint must be served; a 404 is what leaves /runs silently empty",
    ).toBe(200);
  });

  test("the profile shows a stored memory", async ({ page }) => {
    skipIfKnownGap("the profile shows a stored memory");
    await login(page);

    // Asserted at the API rather than through the card, deliberately.
    //
    // The card renders "No memories yet. Chat with the product or review
    // agents to build your profile." both when the endpoint returns an empty
    // list and when it 404s — the UI swallows the error either way. So from
    // the DOM alone a backend with no memories endpoint is indistinguishable
    // from a user who simply has none, which is precisely the silent failure
    // this gate exists to catch. Driving a turn and hoping the model chooses
    // to call store_memory would also make it flaky.
    await page.goto("/profile");
    await expect(page.getByText(/AI Memory/i).first()).toBeVisible({ timeout: 15000 });

    const token = await page.evaluate(() =>
      localStorage.getItem("ecommerce_access_token") ?? "",
    );
    expect(token, "the session token must be readable for the API assertion").not.toBe("");

    const response = await page.request.get("http://localhost:8080/api/user/memories", {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(
      response.status(),
      "the memories endpoint must be served; a 404 is what makes the card's instruction untrue",
    ).toBe(200);
  });

  test("a grounded answer renders the grounding badge", async ({ page }) => {
    skipIfKnownGap("a grounded answer renders the grounding badge");
    await login(page);
    await page.goto("/chat");
    await sendMessageAndWaitForTurn(page, "Show me wireless headphones under $400");

    const badge = page.getByText(/verified against the database/i).first();
    await expect(
      badge,
      "a product answer must report grounding; the badge never rendering is the silent failure",
    ).toBeVisible({ timeout: 20000 });
  });
});
