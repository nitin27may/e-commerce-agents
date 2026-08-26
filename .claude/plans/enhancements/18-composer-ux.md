# Plan 18 — Composer UX (#4)

**Status:** proposed · **Date:** 2026-08-26 · **Issue:** [#4](https://github.com/nitin27may/e-commerce-agents/issues/4)
**Sibling:** [`17-tutorial-dotnet-coverage.md`](17-tutorial-dotnet-coverage.md) — independent, no shared code.
**Scope:** `web/` only. Zero backend change, either stack.

Two independent changes to the chat composer, shippable as two PRs. **Part B first** — it is smaller,
riskier to sequencing, and delivers the visible win.

---

## 1. Measured state, 2026-08-26

| File | Lines | Relevant |
|---|---|---|
| `web/src/components/ui/ai-prompt-box.tsx` | 325 | `AGENT_MODES` :61 · mode chip row :173–191 · suggestion chips :232–256 |
| `web/src/app/(app)/chat/page.tsx` | 900 | `<PromptInputBox>` :887 · `suggestions={DEMO_SCENARIOS.slice(0, 4)}` :890 |
| `web/src/lib/scenarios.ts` | 128 | `DEMO_SCENARIOS` :42 (8 entries) · `QUICK_PROMPTS` :115 |

**`ai-prompt-box.tsx` has no unit test.** 21 other components under `src/components` do. The composer
— the single most-used control in the product — is untested. That is a bigger finding than either
half of #4 and is fixed here regardless of which part ships.

**No E2E spec selects a mode chip.** Verified by grep across all 12 `e2e/*.spec.ts`. So the
refactor is not blocked by test selectors. It *is* coupled to recorded artifacts — see R1.

---

## 2. The constraint

`remaining-work.md` records #4 as **frontend-only by constraint**: no new endpoint, no changed request
shape, no fence-contract change, both backends byte-identically unaffected, verified by the
dual-backend gate.

That rules out issue #4's second option for Part A — "a small structured *suggested next actions*
field the orchestrator/specialist prompts already emit". That would mean a new SSE frame or response
field implemented twice, in Python and .NET, kept at parity, with fixtures re-recorded on both sides.
It is a good idea and a bad fit for this issue.

§4 below gets most of that value with none of the cost, because **the typed payloads already exist on
the client**.

---

## 3. Part B — collapse the mode selector (0.5–1 day, ship first)

### Current

```
┌─────────────────────────────────────────┐
│ [Auto] [Products] [Orders] [Pricing]... │  ← always visible, every message
├─────────────────────────────────────────┤
│  Ask about products, orders, or...      │
├─────────────────────────────────────────┤
│ 📎                                  ➤   │
└─────────────────────────────────────────┘
```

`AGENT_MODES` is a static six-entry array rendered unconditionally as a horizontal pill row above the
textarea. Most users never leave `Auto` — the mode that lets the orchestrator route for them, which
is the whole point of the product.

### Proposed

```
┌─────────────────────────────────────────┐
│  Ask about products, orders, or...      │
├─────────────────────────────────────────┤
│ 📎  ⚙︎ Auto                          ➤   │   ← Auto: label only, no row
└─────────────────────────────────────────┘

┌─────────────────────────────────────────┐
│  Search, compare, or explore...         │
├─────────────────────────────────────────┤
│ 📎  ⚙︎ Products ✕                    ➤   │   ← non-Auto: pill + clear
└─────────────────────────────────────────┘
```

- Mode control moves into the existing bottom action row, beside the attach button.
- Click opens a popover listing the six modes; `Auto` is checked by default.
- When the mode is `Auto`, the control is a plain icon — **the row of vertical space is reclaimed
  entirely**, which is the actual ask.
- When a specialist mode is active, show a compact labelled pill with a clear affordance, so the
  selection is never invisible.
- Placeholder still changes with the mode (`currentMode.placeholder`) — that behaviour is good and
  stays.

### Counter-argument, and the answer

The chip row is one of the few surfaces that visibly says *this is a multi-agent app*. Hiding it
removes a demo cue.

Answer: `ModeSwitcher` and `ModeComparison` stay in the toolbar directly above the composer
(`chat/page.tsx:884–885`), and those are the *orchestration-mode* controls — router / handoff /
workflow / group-chat — which is the far more interesting multi-agent signal. #4 explicitly puts
them out of scope. The agent-mode chips are a routing override, not the story.

### Contract that must not change

- `onSend(message, agentMode)` — signature and emitted values unchanged.
- `agentMode` remains `string | null`, `null` for Auto.
- `PromptInputBoxProps` gains nothing required. If a prop is added it must be optional.

### Accessibility and mobile

- Trigger: `aria-haspopup="menu"`, `aria-expanded`, `aria-label` reflecting the active mode.
- Popover: `role="menu"`, `role="menuitemradio"` per option with `aria-checked`.
- Arrow-key roving focus, `Escape` closes and returns focus to the trigger.
- Touch targets ≥ 44 px. The reclaimed row is worth most on small viewports — that is the win.

### Done when

- [ ] With `Auto` selected, the composer is exactly one row shorter than today (assert on rendered
      structure, not a pixel snapshot).
- [ ] Selecting `Products` still sends `agentMode: "product-discovery"` — asserted in a unit test on
      the `onSend` spy.
- [ ] Keyboard-only operation works: open, arrow, select, escape.
- [ ] `npx vitest run && npx tsc --noEmit && npx eslint .` clean.

---

## 4. Part A — contextual suggestions (1–1.5 days)

### Current

`chat/page.tsx:890` passes `DEMO_SCENARIOS.slice(0, 4)` — the same four canned prompts in every
conversation, forever. `ai-prompt-box.tsx:249` then renders `suggestions.slice(0, 3)`, so **the
fourth is computed and thrown away** on every render. Minor, but it tells you nobody has looked at
this path.

### The better source than prose

Issue #4 proposes regex over the assistant's closing question. That works, but there is a stronger
signal already on the client: **the message's typed generative-UI payload.** `src/lib/chat-schemas.ts`
already parses assistant messages into typed card data — product, order, pricing, inventory,
sentiment — each with its own component and its own test.

Deriving suggestions from the *payload type* is deterministic, trivially unit-testable, and immune to
model phrasing or language. Prose parsing becomes a secondary source, not the primary one.

### Design

New pure module `web/src/lib/suggestions.ts`:

```ts
export function deriveSuggestions(
  last: AssistantMessage | undefined,
  fallback: Suggestion[],
): Suggestion[]
```

Three tiers, first non-empty wins, always padded to 3 from `fallback`:

**Tier 1 — payload-driven (primary).** Map the rendered card type to follow-ups:

| Payload in last message | Suggestions |
|---|---|
| product card(s), >1 | "Compare these", "Check stock", "Any deals?" |
| product card, exactly 1 | "Check stock", "Show reviews", "Find similar" |
| order card | "Track this order", "Start a return", "Order status" |
| pricing card | "Any better deals?", "Price history", "Apply a coupon" |
| inventory card | "Estimate delivery", "Other warehouses", "Notify when in stock" |
| sentiment card | "Show negative reviews", "Summarise the pros", "Compare sentiment" |

**Tier 2 — closing question (secondary).** If there is no payload, extract from the last ~200 chars:

- `Would you like to X or Y?` → chips `X`, `Y`
- `Do you want me to X?` → chip `Yes, X`
- `1. X  2. Y  3. Z` → chips `X`, `Y`, `Z`

Hard rules, because this tier is the one that can embarrass you:
- Only the final sentence, only if it ends in `?`.
- Reject candidates > 42 chars or < 3 chars.
- Strip trailing punctuation; sentence-case; never send raw model text unedited.
- If extraction yields fewer than 2 clean candidates, discard the tier entirely and fall through.

**Tier 3 — fallback.** Today's `DEMO_SCENARIOS.slice(0, 3)`. Unchanged behaviour, so a conversation
with no signal looks exactly as it does now.

### Click behaviour

Keep today's behaviour: clicking a chip **pre-fills the textarea and focuses it**
(`ai-prompt-box.tsx:250`). Do not auto-send. The user can edit, and a wrong derived suggestion costs
a keystroke instead of a wasted LLM call.

### Why a pure function matters

`deriveSuggestions` takes data and returns data. It needs no React, no network, no fixtures — so the
whole feature is covered by fast unit tests, which is the same argument §3 of plan 17 makes for the
.NET tutorial tests. Consistent testing philosophy across the repo.

### Done when

- [ ] `suggestions.ts` has ≥ 15 unit tests covering each payload type, each prose pattern, the
      rejection rules, and the fallback.
- [ ] Chips always number exactly 3, never duplicate, never empty.
- [ ] `chat/page.tsx` passes `deriveSuggestions(lastAssistantMessage, DEMO_SCENARIOS)`; the
      `.slice(0, 4)`-then-`.slice(0, 3)` waste is gone.
- [ ] With an empty conversation, the chips are byte-identical to today's.

---

## 5. Tests to add

| File | Covers |
|---|---|
| `src/lib/suggestions.test.ts` | new — the full tier table above |
| `src/components/ui/ai-prompt-box.test.tsx` | **new — the composer has no test today**: renders; `onSend` receives `(text, mode)`; Enter sends and Shift+Enter does not; chips render only when input is empty and not loading; mode popover opens/selects/closes; disabled while loading |
| `src/lib/scenarios.test.ts` | existing — assert `DEMO_SCENARIOS` is still the fallback source so the contract is pinned |

No new E2E spec. `e2e/chat-ui-verify.spec.ts` and `ui-features.spec.ts` already exercise the chat
surface and must stay green unmodified — that is the regression signal.

---

## 6. Risks

**R1 — Sequencing against the demo clip (the real one).** `e2e/demo-recording.spec.ts` and
`e2e/readme-screenshots.spec.ts` generate the recorded artifacts, and audit finding F3 / plan 14 item
3 schedule a 60–90 s silent clip for the README and site home. **That clip shows the composer.** Land
#4 *before* recording, or budget a re-record. This is the strongest argument for doing #4 now rather
than later, and it is not mentioned anywhere in plan 14.

**R2 — A derived suggestion that reads as a bug.** A bad Tier-2 extraction ("Would you like to see
more options or compare this with other" truncated mid-phrase) looks worse than the static chips it
replaced. Mitigated by the length/punctuation rejection rules, and by pre-fill-not-send. If Tier 2
proves noisy in live testing, **ship Tier 1 + Tier 3 only** — that alone closes most of #4A and
carries near-zero risk.

**R3 — Discoverability of specialist modes drops.** Accepted, with the labelled-pill mitigation in
§3. Worth a look at the usage table after a few weeks — if nobody ever selected a non-Auto mode, the
control could arguably leave the composer entirely.

**R4 — Scope creep into the orchestration controls.** `ModeSwitcher` / `ModeComparison` are
explicitly out of scope per #4. Do not "tidy" them in the same PR.

**R5 — `NEXT_PUBLIC_*` build-time inlining.** Unrelated to this change, but it is how a dual-backend
run has silently lied before. Use `NEXT_DIST_DIR` if running a second dev frontend side by side.

---

## 7. Effort

| Part | Work | Effort |
|---|---|---|
| B | Collapse mode selector + a11y | 0.5–1 d |
| A | `suggestions.ts` + wire-up | 1–1.5 d |
| — | `ai-prompt-box.test.tsx` (net-new coverage) | 0.5 d |
| | **Total** | **2–3 days** |

Two PRs: `feat/composer-mode-collapse`, then `feat/composer-contextual-suggestions`.

The audit called #4 "a two-hour job that will not move a single bookmark." That estimate was low —
it did not account for the composer having no tests — and the conclusion misses R1: this ships before
the clip is recorded, or it costs a re-record.

---

## 8. Verification

```bash
cd web
npx vitest run          # unit — must include the two new files
npx tsc --noEmit        # strict mode
npx eslint .
npx playwright test e2e/chat-ui-verify.spec.ts e2e/ui-features.spec.ts   # unmodified, must stay green
```

Then, per `remaining-work.md`'s standing rule, **exercise it against a running stack** — mode
selection, a real reply with product cards, and the derived chips. Live runs catch what tests cannot.

---

## 9. Handover notes

`web/**` ownership is not assigned in `remaining-work.md`'s split — confirm before starting that no
other session holds the working tree. **Stage explicit paths; never `git add -A`** (that has already
swept 35 files from another session into an unrelated commit). Commit or stash before switching
branches.
