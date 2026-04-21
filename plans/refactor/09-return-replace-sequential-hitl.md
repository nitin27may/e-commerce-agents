# Refactor 09 — Return/Replace to Sequential + HITL

## Goal

Replace `agents/workflows/return_replace.py:37` with MAF Sequential orchestration plus a Human-in-the-Loop approval gate for high-value returns.

## Deliverables

- `agents/workflows/return_replace.py` rewritten:
  - Sequential steps: `CheckEligibility` → `InitiateReturn` → `SearchReplacements` → **HITL gate** → `ApplyDiscount` → `FinalizeReturn`.
  - HITL fires when `order_total > RETURN_HITL_THRESHOLD` (default 500).
  - Uses `ctx.request_info()` to emit a `confirm_return` event carrying the proposed discount and replacement options.
  - Resumes on user response via `POST /api/workflows/return/{workflow_id}/respond`.
- Checkpointing enabled (from sub-plan `11`): state saved after each step; abandoned returns resume on reconnect.
- **Frontend**:
  - `web/components/chat/confirm-return-card.tsx` — renders the pending approval.
  - Chat stream consumes `RequestInfoEvent` payloads and renders the card.
- New Postgres table: `hitl_requests` (see `init.sql` addendum in sub-plan `11`).

## Test file

`agents/tests/test_workflow_return_replace.py` — minimum 7 tests:

- HITL fires above threshold; auto-approves below.
- After response, sequence continues; workflow reaches `FinalizeReturn`.
- Checkpoint persists mid-sequence; new process restores and continues.
- Timeout on HITL produces a well-defined cancellation event.
- Invalid user response (wrong shape) rejected cleanly without losing state.
- Parity: low-value returns produce same final order state as before.
- Replacements list matches pre-refactor for the same seeded order.

## Playwright E2E

`web/e2e/workflow-return-hitl.spec.ts` — returns for a high-value order; confirms UI card renders; clicking "Approve" completes the return.

## Verification

- Aspire shows the full sequence with a visible pause span for HITL.
- Mermaid diagram at `docs/workflows/return_replace.mmd`.

## Out of scope

- Auto-approval heuristics (e.g., "auto-approve if trusted user") — noted as follow-up.
