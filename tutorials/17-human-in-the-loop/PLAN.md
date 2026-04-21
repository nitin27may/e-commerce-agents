# Chapter 17 — Human-in-the-Loop

## Goal

Teach `RequestPort` / `ctx.request_info()` — pause a workflow to ask a human, resume when the human answers.

## Article mapping

- **New chapter** (not covered in old series)
- **New slug**: `/posts/maf-v1-human-in-the-loop/`

## Teaching strategy

- [x] New mini-example + refactor hook — Ch17 teaches HITL with a guessing game; Phase 7 `plans/refactor/09-return-replace-sequential-hitl.md` applies the pattern to the real return/replace workflow with an approval gate on high-value returns.

## Deliverables

### `python/`
- `main.py` — number-guessing game: workflow asks the human to guess, receives the guess via `ctx.request_info()`, judges.
- `tests/test_hitl.py` — ≥ 4 tests: pause emits `RequestInfoEvent`; resume with response continues; multiple roundtrips work; timeout semantics (if present).

### `dotnet/`
- `RequestPort<int, string>` + `RequestInfoEvent` loop.

### Article
- Pause/resume semantics.
- Where UIs plug in — show the SSE event shape the frontend receives.

## Verification

- An interactive run prompts for a guess, accepts it, reports "higher/lower" until the user wins.

## How this maps into the capstone

Phase 7 `plans/refactor/09-return-replace-sequential-hitl.md` adds a HITL gate before `ApplyDiscount` when `order_total > RETURN_HITL_THRESHOLD`. The frontend renders a `ConfirmReturnCard` from the `RequestInfoEvent`.

## Out of scope

- Tool approval `@tool(approval_mode="always_require")` — brief pointer; the mechanism is the same `RequestInfoEvent`.
