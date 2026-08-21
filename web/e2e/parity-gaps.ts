import { test } from "@playwright/test";

/**
 * Dual-backend parity gate.
 *
 * There is one frontend, and `NEXT_PUBLIC_BACKEND_STACK` points it at either
 * the Python or the .NET orchestrator. So the honest definition of "is .NET
 * done?" is not a test count or a row in a matrix — it is *this suite passing
 * against both backends*.
 *
 * Why this file exists rather than scattered `test.skip()` calls: a skipped
 * test with no recorded reason is indistinguishable from a test nobody ever
 * wrote. Every entry below names the issue that will delete it. The list is
 * the parity checklist, executable — **when `dotnet` is empty, .NET parity is
 * done**.
 *
 * The failure mode this guards against is specific and already observed: the
 * existing 109-test e2e suite passes green against a .NET backend that is
 * missing four whole features, because it never exercises them. Assertions
 * here must therefore check for *presence* — a test that only confirms "no
 * error" would go green against a blank page.
 */

export type BackendStack = "python" | "dotnet";

export const BACKEND: BackendStack =
  process.env.BACKEND_STACK === "dotnet" ? "dotnet" : "python";

/**
 * Known gaps, keyed by backend then by test title. The value is the reason,
 * and must reference a tracking issue — "not implemented" alone is how a gap
 * becomes permanent.
 *
 * Delete a line here as part of the PR that closes it. Nothing else needs to
 * change; the test simply starts running.
 */
export const PARITY_GAPS: Record<BackendStack, Record<string, string>> = {
  python: {
    // Python is the reference implementation. An entry appearing here means
    // the *frontend* expects something neither backend provides, which is a
    // different bug — treat it as a red flag rather than a normal gap.
  },

  dotnet: {
    // Empty. Every surface this gate checks is served by both backends.
    //
    // This is the exit criterion Phase 14 was written around: not a test
    // count and not a row in docs/parity-matrix.md, but this object being
    // empty while every test above still asserts presence. Items that are
    // still python-first (handoff and group-chat modes, magentic, the
    // evals harness) are tracked on the umbrella issue — they are absent
    // from this gate because no test here exercises them, which is an
    // honest gap in coverage, not a claim that they exist.
  },
};

/**
 * Skips the current test when its title has a recorded gap for this backend,
 * with the reason attached — the run summary reads
 * `known dotnet gap → #33 PR 5 — …` rather than a silent dash.
 *
 * Called as the first line of a test body rather than wrapping `test()`,
 * because Playwright statically requires a test's first argument to use the
 * object-destructuring pattern and so cannot accept a forwarded fixtures
 * object from a helper.
 */
export function skipIfKnownGap(title: string) {
  const gap = PARITY_GAPS[BACKEND][title];
  test.skip(Boolean(gap), `known ${BACKEND} gap → ${gap}`);
}

/**
 * Fails if a gap is recorded for a test title that no longer exists — a
 * renamed or deleted test would otherwise leave a stale entry that silently
 * suppresses nothing while still counting as an open gap.
 */
export function assertGapsAreLive(declaredTitles: string[]) {
  const declared = new Set(declaredTitles);
  const stale = Object.keys(PARITY_GAPS[BACKEND]).filter((t) => !declared.has(t));
  if (stale.length > 0) {
    throw new Error(
      `PARITY_GAPS[${BACKEND}] references tests that no longer exist: ${stale.join(", ")}. ` +
        `Remove the stale entries.`,
    );
  }
}
