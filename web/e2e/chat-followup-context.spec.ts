import { test, expect, type Page } from "@playwright/test";

/**
 * Issue #9 — a follow-up question must be answered using the previous turn.
 *
 * This is the coverage whose absence let #9 ship: `web/e2e/` had no
 * multi-turn spec at all, and every unit test that appeared to cover the
 * session plumbing set the ContextVar or the header by hand, so all of them
 * passed while specialists received no history whatsoever.
 *
 * The assertion deliberately targets the *specialist's* behaviour rather than
 * the orchestrator's. The orchestrator always held the conversation history,
 * and its prompt asks it to inline context into the specialist message — a
 * non-deterministic instruction that sometimes worked. That is exactly why
 * the bug read as "LLM nondeterminism" for so long. A follow-up that names
 * nothing ("which of those…") only resolves if real context reached the
 * agent that answered it.
 *
 * Scope, stated honestly: this drives the whole stack, so the orchestrator's
 * own inlining can still mask a broken specialist tier on a lucky run — it is
 * a smoke-level guard, not the deterministic proof. That proof is
 * agents/python/tests/test_chat_specialist_context.py (asserts the session id
 * on the outbound A2A headers) plus a direct /message:send call to a
 * specialist, which reproduces the reported symptom verbatim when the session
 * id is empty and answers correctly when it is not.
 *
 * Requires a configured LLM; a misconfigured stack fails loudly here rather
 * than passing on an error string.
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
  // Wait on the composer's stop icon, not "Routing to specialists…" — the
  // latter only signals that streaming started, so a follow-up fired on it
  // silently no-ops against sendMessage's isResponding guard.
  const stopButton = page.locator("button.bg-red-500");
  await stopButton.waitFor({ state: "visible", timeout: 10000 }).catch(() => {});
  await stopButton.waitFor({ state: "hidden", timeout: 90000 });
}

async function lastAssistantText(page: Page): Promise<string> {
  // Assistant bubbles only: the same rounded-2xl shape is used by the
  // composer, so a bare [class*="rounded-2xl"] returns the prompt chrome
  // ("auto products orders pricing … enter to send") instead of a reply.
  // bg-muted is the assistant variant; user turns are bg-primary.
  const bubbles = page.locator("div.rounded-2xl.bg-muted");
  const count = await bubbles.count();
  expect(count, "no assistant message rendered").toBeGreaterThan(0);
  return (await bubbles.nth(count - 1).textContent()) ?? "";
}

test.describe("Follow-up questions keep conversation context (#9)", () => {
  test.setTimeout(180000);

  test("a follow-up that names no product still resolves to the one just shown", async ({ page }) => {
    await login(page, "alice.johnson@gmail.com", "customer123");
    await page.goto("/chat");

    await sendMessageAndWaitForTurn(page, "Show me the Sony WH-1000XM5 headphones");
    const first = await lastAssistantText(page);
    expect(first.toLowerCase()).toContain("sony");

    // Names no product, no id, no category. Only prior context can resolve it.
    await sendMessageAndWaitForTurn(page, "How long does its battery last?");
    const second = await lastAssistantText(page);

    // The bug's signature was a generic non-sequitur — a fresh broad search
    // ("I couldn't find any headphones under $350…") or a request to repeat
    // information already on screen.
    expect(second).not.toMatch(/couldn't find|could not find|unable to find/i);
    expect(second).not.toMatch(/which product|which item|please specify|can you clarify/i);
    expect(second.length).toBeGreaterThan(20);
  });

  test("a second turn is durable immediately, so a third turn still sees it", async ({ page }) => {
    // Covers the other half of #9: the assistant turn used to be persisted by
    // a task spawned *after* [DONE], and [DONE] is what re-enables the
    // composer — so a fast follow-up could read history before that INSERT
    // committed and lose the turn it was following up on.
    await login(page, "alice.johnson@gmail.com", "customer123");
    await page.goto("/chat");

    await sendMessageAndWaitForTurn(page, "My name for this chat is Wombat. Remember it.");
    await sendMessageAndWaitForTurn(page, "What noise cancelling headphones do you have?");
    await sendMessageAndWaitForTurn(page, "What name did I give you at the start of this chat?");

    const answer = await lastAssistantText(page);
    expect(answer.toLowerCase()).toContain("wombat");
  });
});
