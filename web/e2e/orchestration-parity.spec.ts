import { test, expect, type Page } from "@playwright/test";
import { BACKEND, skipIfKnownGap } from "./parity-gaps";
import { staffPassword } from "./credentials";

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
// /runs is a staff page (components/staff-only.tsx).
const ADMIN = { email: "admin.demo@gmail.com", password: staffPassword("admin123") };

/**
 * The orchestrator the API-level assertions talk to. Defaults to the port the
 * compose stacks publish, and is overridable so a backend can be exercised
 * out-of-band — running .NET on another port while the frontend still points
 * at Python would otherwise make those assertions silently verify the wrong
 * backend and report a false pass.
 */
const ORCH_URL = process.env.ORCH_URL ?? "http://localhost:8080";

async function login(page: Page, user: { email: string; password: string } = CUSTOMER) {
  await page.goto("/login");
  await page.fill('input[type="email"]', user.email);
  await page.fill('input[type="password"]', user.password);
  await page.click('button[type="submit"]');
  await page.waitForURL(/\/(chat|products|home|admin)/, { timeout: 15000 });
  await assertFrontendTalksToOrchUrl(page);
}

/**
 * Fails loudly when the frontend under test is pointed at a *different*
 * orchestrator than `ORCH_URL`.
 *
 * This is not hypothetical: that exact false pass happened twice while
 * building this gate. The original cause is now gone — the backend address
 * used to be `NEXT_PUBLIC_API_URL`, inlined into the bundle at build time, so
 * a second dev server booting off a warm build directory silently served the
 * first one's backend. Since the frontend proxies `/api/*` server-side, the
 * address is `ORCHESTRATOR_URL`, read at runtime, and no build can encode the
 * wrong backend.
 *
 * The guard stays because the failure did not go away, it moved: a frontend
 * started with the wrong `ORCHESTRATOR_URL` fails in exactly the same shape —
 * login against backend A, every API-level assertion here against backend B,
 * and a green run that never touched the backend it claims. The token is still
 * the cheapest discriminator: it is signed by whichever backend served the
 * login, so a 401 from `ORCH_URL` means the two are not the same process.
 */
async function assertFrontendTalksToOrchUrl(page: Page) {
  const token = await page.evaluate(
    () => localStorage.getItem("ecommerce_access_token") ?? "",
  );
  const probe = await page.request.get(`${ORCH_URL}/api/runs`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(
    probe.status(),
    `the frontend at ${test.info().project.use.baseURL} issued a token that ` +
      `ORCH_URL (${ORCH_URL}) rejects, so they are different backends. ` +
      `This run would test one backend while claiming to test the other — ` +
      `set E2E_BASE_URL (not PLAYWRIGHT_BASE_URL) to the frontend running ` +
      `with ORCHESTRATOR_URL pointed at BACKEND_STACK=${BACKEND}.`,
  ).not.toBe(401);
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

  // The orchestration modes are a backend capability. The storefront chat offers no mode switcher, graph or
  // Compare dialog (no role sees agent internals there), so these four check the API both stacks serve; the
  // evals (`--mode`) and DevUI drive the same endpoints.

  async function token(page: Page) {
    return page.evaluate(() => localStorage.getItem("ecommerce_access_token") ?? "");
  }

  test("the backend offers more than one orchestration mode", async ({ page }) => {
    skipIfKnownGap("the backend offers more than one orchestration mode");
    await login(page);
    const res = await page.request.get(`${ORCH_URL}/api/orchestration/modes`, {
      headers: { Authorization: `Bearer ${await token(page)}` },
    });
    expect(res.ok(), "GET /api/orchestration/modes").toBe(true);
    const names = ((await res.json()) as { name: string }[]).map((m) => m.name);
    expect(names, "the tool router is offered").toContain("tool");
    expect(names.some((n) => n.startsWith("workflow:")), "a workflow mode is offered").toBe(true);
  });

  test("selecting a workflow mode is honoured, not silently downgraded", async ({ page }) => {
    skipIfKnownGap("selecting a workflow mode is honoured, not silently downgraded");
    await login(page);
    const res = await page.request.post(`${ORCH_URL}/api/chat`, {
      headers: { Authorization: `Bearer ${await token(page)}` },
      data: { message: "Should I buy the Sony WH-1000XM5?", mode: "workflow:pre-purchase" },
      timeout: 120_000,
    });
    expect(res.ok(), "POST /api/chat in workflow:pre-purchase").toBe(true);
    const body = (await res.json()) as { agents_involved: string[] };
    // The pre-purchase graph's own nodes; a backend that drops `mode` answers in tool mode instead.
    expect(
      body.agents_involved.some((a) => /reviews|stock|price|synthes/i.test(a)),
      `the pre-purchase graph ran, not the tool router (got ${body.agents_involved.join(", ")})`,
    ).toBe(true);
  });

  test("the graph for a workflow mode is served", async ({ page }) => {
    skipIfKnownGap("the graph for a workflow mode is served");
    await login(page);
    const res = await page.request.get(
      `${ORCH_URL}/api/orchestration/modes/${encodeURIComponent("workflow:pre-purchase")}/graph`,
      { headers: { Authorization: `Bearer ${await token(page)}` } },
    );
    expect(res.ok(), "GET .../modes/workflow:pre-purchase/graph").toBe(true);
    const body = (await res.json()) as { mermaid: string | null };
    expect(body.mermaid, "a workflow mode has a graph").toBeTruthy();
  });

  test("mode comparison runs two modes", async ({ page }) => {
    skipIfKnownGap("mode comparison runs two modes");
    await login(page);
    const res = await page.request.post(`${ORCH_URL}/api/orchestration/compare`, {
      headers: { Authorization: `Bearer ${await token(page)}` },
      data: { message: "Should I buy the Dyson V15?", modes: ["workflow:pre-purchase", "workflow:return-replace"] },
      timeout: 120_000,
    });
    expect(res.ok(), "POST /api/orchestration/compare").toBe(true);
    const body = (await res.json()) as { results: { mode: string; error: string | null }[] };
    expect(body.results.map((r) => r.mode).sort()).toEqual(["workflow:pre-purchase", "workflow:return-replace"]);
  });

  test("a run's checkpoints are listed on /runs", async ({ page }) => {
    skipIfKnownGap("a run's checkpoints are listed on /runs");
    await login(page, ADMIN);

    // Deliberately does NOT create a workflow run first. Doing so would
    // require selecting a workflow mode, which couples this test to the mode
    // registry and makes it untestable on a backend that has the checkpoints
    // endpoint but not modes yet — two independent capabilities failing as
    // one. Any existing run is enough to prove the endpoint is served.
    await page.goto("/runs");
    await expect(page.getByText(/Agent Runs/i).first()).toBeVisible({ timeout: 20000 });

    // Asserted at the API because /runs absorbs a 404 in `.catch(() => {})`:
    // the page renders perfectly and the checkpoint panel is simply empty, so
    // from the DOM alone "no endpoint" and "no checkpoints" are identical.
    // That indistinguishability is the silent failure being guarded against.
    const token = await page.evaluate(() =>
      localStorage.getItem("ecommerce_access_token") ?? "",
    );
    const runs = await page.request.get(`${ORCH_URL}/api/runs`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(runs.status()).toBe(200);

    const runId = (await runs.json()).entries?.[0]?.id;
    expect(runId, "there must be at least one run to inspect").toBeTruthy();

    const checkpoints = await page.request.get(`${ORCH_URL}/api/runs/${runId}/checkpoints`, {
      headers: { Authorization: `Bearer ${token}` },
    });
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

    const response = await page.request.get(`${ORCH_URL}/api/user/memories`, {
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
