# Plan 18 — Composer UX

**Status:** proposed · **Date:** 2026-08-26 · **Issue:** [#4](https://github.com/nitin27may/e-commerce-agents/issues/4)
**Effort:** 1–2 days · **Target:** v1.2.1 or v1.3.0

Two changes to the chat composer: collapse the always-visible mode chips, and make the suggested
prompts follow the conversation instead of ignoring it.

---

## The hard constraint

**Frontend-only.** No new endpoint, no changed request shape, no change to the fence contract. Both
backends must be byte-identically unaffected, verified by the dual-backend Playwright gate.

That constraint is what keeps this a day of work rather than a week, and it is worth restating in
the PR because both changes are tempting to solve server-side.

---

## Where the code is

| Concern | Location |
|---|---|
| Mode chip row | `web/src/components/ui/ai-prompt-box.tsx:175` (`AGENT_MODES.map(...)`) |
| Mode definitions | `web/src/components/ui/ai-prompt-box.tsx:61` |
| Static suggestions | `web/src/app/(app)/chat/page.tsx:890` — `DEMO_SCENARIOS.slice(0, 4)` |
| Scenario source | `web/src/lib/scenarios.ts` |
| An existing chip component | `web/src/components/chat/action-chips.tsx` (`chips: string[]`, `onChipClick`) |
| Mode switcher (separate) | `web/src/components/chat/mode-switcher.tsx` |

Note there are **two** mode surfaces: `AGENT_MODES` in `ai-prompt-box.tsx` (the chip row this plan
collapses) and `mode-switcher.tsx` (the orchestration-mode control added in Phase 1.6a). They are
different things. Confirm which one issue #4 means before moving anything — collapsing the wrong one
would hide the orchestration-mode selector, which is a headline feature and the thing the demo clip
is built around.

---

## Part 1 — collapse the mode chips

Today every mode is rendered as a chip, always visible, consuming vertical space above the input on
every turn.

**Proposal:** show the active mode only, as a single control that opens the full list on click.
Keyboard-reachable, current selection announced.

**Decisions to make before implementing:**

- Popover, dropdown, or inline expand? The repo already uses shadcn/ui, so a `Popover` or
  `DropdownMenu` is the least new surface area.
- Does the collapsed state persist per conversation, like the orchestration mode does in
  `localStorage`? Probably yes, for consistency.
- What shows on first visit — collapsed, or expanded once to teach the feature exists? Collapsing a
  feature nobody has discovered yet is how features die.

**Risk worth naming:** the modes are a differentiator. The audit's whole argument is that this repo
solves one domain five ways and under-markets it. Hiding the control could make the product feel
simpler and the project less distinctive. Mitigate by keeping the *active* mode visible and legible,
not reducing it to an unlabelled icon.

---

## Part 2 — contextual suggestions

Today: `DEMO_SCENARIOS.slice(0, 4)` — the same four prompts regardless of what is on screen. After a
product answer it still offers "track my order".

**Proposal:** derive suggestions from the last assistant message, client-side.

Deliberately *not* an LLM call. A second model round-trip per turn would add latency and cost to
every conversation for a convenience feature, and it would need a new endpoint — which the
constraint forbids.

**Shape:** the frontend already parses assistant messages by shape to render cards, tables and
charts (that is what generative UI *is*). Reuse that classification:

| Rendered content | Suggested next prompts |
|---|---|
| Product cards | "Compare the first two", "Is this in stock?", "Any discounts?" |
| Order card | "Where is it now?", "I want to return this" |
| Return / refund flow | "What is the status of my return?" |
| Review sentiment | "What are the common complaints?" |
| Nothing recognised | fall back to today's `DEMO_SCENARIOS.slice(0, 4)` |

The fallback matters — an empty suggestion row is worse than a static one.

**Reuse `ActionChips`** rather than adding a component. It already takes `chips: string[]` and
`onChipClick`, which is exactly this shape.

---

## Verification

- [ ] `pnpm test` — unit tests for the suggestion mapper, including the no-match fallback
- [ ] `pnpm exec tsc --noEmit`, `pnpm lint`, `pnpm build`
- [ ] **Dual-backend Playwright gate passes against both stacks**, with the same failure set —
      a shared failure is not a parity gap
- [ ] Confirm the request body sent to `/api/chat` and `/api/chat/stream` is **unchanged**. This is
      the constraint; verify it by diffing a captured request, not by reading the code
- [ ] Keyboard navigation and screen-reader labelling on the collapsed control
- [ ] Re-record the demo clip (`web/e2e/demo-recording.spec.ts`) — it drives the composer directly
      and will break, or silently record the wrong thing, if selectors move

That last item is easy to forget and produces a stale clip on the README, which is the first thing a
visitor sees.

---

## Sequencing note

Part 2 is the higher-value half — it makes the assistant feel responsive rather than scripted, and
it is the part a visitor notices in the first ten seconds. Part 1 is tidying.

If only one ships, ship Part 2.
