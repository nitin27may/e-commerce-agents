# Merchant proposals

A seller's copilot for the catalogue (#93): the system looks at stock and recent sales, **proposes**
price markdowns and restock orders, and the seller reviews them line by line. Nothing changes until
the seller accepts it, and nothing the seller accepts can break a hard limit.

It is built without a model. Proposal generation is plain, deterministic code over the seeded data;
the model-driven merchant-ops agent and its eval dataset (plan rows M-2) need a recording session
with a key and are not part of this.

## The flow

```
generate  ──►  proposals + proposal_lines  ──►  one approvals row  ──►  seller decides each line
(manual or      (guards judged every line)       (kind merchant_proposal,   accept | edit | reject
 daily run)                                       source proposal)                │
                                                                                  ▼
                                              re-check guards on live facts, then run once per line:
                                              price update + price_history  |  restock_schedule row
```

| Step | Code |
|---|---|
| Signals, candidates (pure), guards, storage | `agents/python/shared/merchant/proposals.py` |
| Execution of one line | `agents/python/shared/merchant/execute.py` |
| List, generate, decide | `agents/python/orchestrator/proposals_service.py`, `routes/proposals.py` |
| Daily run, SLA reminder/expiry | `agents/python/worker/merchant_handlers.py`, `worker/approval_handlers.py` |
| Hard limits (M-1) | `config/policies/merchant_limits.yaml`, `shared/merchant/guards.py` |
| Seller page | `web/src/app/(app)/seller/proposals/page.tsx` |

## What is proposed

Per active product of the seller, over a 90-day window of orders that were not cancelled or returned:

* **Restock** when any warehouse is below its reorder threshold and no restock is already scheduled:
  order enough to bring each short warehouse back to 3 x its threshold.
* **Markdown** when the product has at least 20 units and either sold nothing (10 %) or has 180 days
  or more of cover (5 %, or 10 % from 365 days). Rounded *up* to the cent so the change never exceeds
  the percentage it was sized for.
* Never both for one product. A batch holds every actionable restock, the 8 most urgent markdowns and
  up to 3 blocked lines (so the seller sees why something was not proposed), cut to
  `max_lines_per_batch`. A batch with nothing the seller could accept is not stored. A (product,
  field) that already has an open line is not proposed again, so generating twice makes one batch.

The numbers are the builder's heuristics (`GenerationRules`); the **limits** are in
`merchant_limits.yaml` and are hard: margin floor, daily change cap, no change during an active
promotion, lines per batch. Each line stores the guard verdict (`proposal_lines.guard_result`); a
line that fails is `blocked` and can only be rejected.

## Review runs on the existing approvals machinery

There is no second approval system. A proposal batch is one `approvals` row, kind `merchant_proposal`
(settings in `approvals.yaml`: editable field `final_value`, 72-hour SLA, `expire` on timeout),
source `proposal`, `source_ref` the proposal id. The seller's decision moves that row with the same
`repo.claim`, guarded `repo.transition` and audit events as every other kind.

### Reviewer is never the requester

The repository and the database CHECK (`decided_by <> requester_email`) refuse a decider equal to the
requester. A proposal is authored by the system, not by whoever pressed "Generate" (and a scheduled
run has nobody), so its **requester is the fixed system actor `merchant-ops@system`** and the
seller is the approval's `subject_email` and its reviewer. The rule stays exactly as it is for
customer refunds; it simply never fires against the seller, and the system actor can never decide.
The seller's own limits are the guards, not the approval rule.

### Who may decide

Only the seller who owns the proposal, or an `admin`. Another seller's proposal is a **404**,
identical to a missing one. Customers and `support` get 403 (`require_seller`). The staff approvals
inbox leaves proposals out; its list omits them, its detail read, decide and answer-info endpoints all answer the same 404 a missing id gets, for every decision (both stacks), so there is one place to decide them.

## Deciding

`POST /api/seller/proposals/{id}/decide` takes `{lines: [{line_id, decision, value?}], note?}`:

| decision | effect |
|---|---|
| `accept` | execute at the proposed value |
| `edit` | execute at `value` (a restock quantity must be whole); recorded as approved with edits |
| `reject` | nothing changes |

**An edit can never raise a line** (the repo-wide rule). A restock quantity may be lowered, never raised above what was proposed; a price must stay between the proposed price and the price it started from (a markdown may be shallower than proposed, never deeper and never above where it began). A value outside that, past `NUMERIC(12,2)` or an INT32 quantity, not finite, or that rounds to zero is a **422** at decide time. `apply_line` checks the same rule again (`edit_beyond_proposal`) so a caller that bypasses the route cannot get past it.

A line left out is rejected. A blocked line can only be rejected (422 otherwise).

Execution, per accepted line (`execute_line`, run once under key `proposal_line:{id}`):

1. lock the line and the product; refuse (`failed`, `not_owner`) if the product no longer belongs to
   the proposal's seller;
2. **re-run the guards against the facts as they are now**: a price that moved, a promotion that
   started or a change already made today since the batch was proposed all count. A failing line
   becomes `blocked` and changes nothing, whatever the seller typed;
3. block, changing nothing, if the product's price is no longer the one the line was proposed against (`price_moved`; the UPDATE is also conditional on it), or, for a restock, stock is already on order (`restock_already_scheduled`) or the line is over `max_restock_quantity` / `max_restock_order_value`;
4. write: `products.price` and a `price_history` row, or one `restock_schedule` row (to the warehouse
   furthest below its threshold, expected 7 days out).

The approval is recorded **after** execution (as tool approvals are), fenced by the claim token: a
crash leaves it open and a retry finishes without repeating anything, two concurrent decides give one
200 and one 409, and a repeat after completion is a 409. The approval ends `executed` if any line ran,
`failed` if every accepted line was blocked, `rejected` if nothing was accepted.

### Generation is serialised per seller

`generate_proposal` takes a per-seller `pg_advisory_xact_lock` before it reads "already proposed", so two concurrent runs (a click and the daily timer) make one batch and a restock is never ordered twice. A unique index on open lines is not expressible: a line's status does not say whether its proposal is still open (lines of an expired batch stay `proposed`).

## API

| Route | Who |
|---|---|
| `GET /api/seller/proposals` | seller (own), admin (all) |
| `POST /api/seller/proposals/run` | seller for themselves; admin with `{seller_email}` |
| `GET /api/seller/proposals/{id}` | owner, admin; others 404 |
| `POST /api/seller/proposals/{id}/decide` | owner, admin; others 404 |

## The daily run (M-4)

`merchant.daily_run_due` is a self-rescheduling outbox timer, **off by default**
(`MERCHANT_OPS_SCHEDULE_ENABLED=false`; run hour `MERCHANT_OPS_RUN_HOUR_UTC`, default 6). The worker
books the next run at start when enabled. Each run generates for every seller with products, each in
its own savepoint (one failing seller does not stop the others), then books the next day in the same
transaction under a per-date dedupe key. With the flag off a stray row does nothing and does not
reschedule. The flag exists because the keyed merchant-ops agent (M-2), when it lands, will spend
model money on a scheduled run; today's generation costs nothing.

### SLA expiry and a decide in flight

The breach timer does not expire a batch while a decide holds the claim or any line is already `accepted`, `edited` or `executed`: it re-books itself every 30 s. While the decide still holds a live claim the timer always waits (the claim's own lifetime, `HITL_CLAIM_TTL_SECONDS`, bounds that); once the claim is gone it looks at most 10 more times. When it does close the batch it locks the lines and counts again first, so a line committed at the last moment is reported. If lines ran and no decide finishes, the batch closes with the approval `expired` (decision records `executed_lines`), the proposal `executed`, and the seller told how many changes were applied. "Nothing was changed" is only said when nothing ran.

An unanswered batch is reminded at half its SLA (notification to the seller) and expires at 72 hours:
approval `expired`, proposal `cancelled`, nothing executed, the seller told.

## Not done here, and parity

* **M-2** (merchant-ops `Agent`, `merchant-ops.yaml`, `propose_markdown` / `propose_restock` tools,
  `evals/datasets/merchant_ops.json`) needs a recording session. No `@tool` was added, so no existing
  agent's tool schema, prompt or replay fixture changed. When M-2 wraps `generate_proposal`, its tool
  output carries seller-authored product names and so needs a sanitizer entry.
* Python-only by recorded decision (parity matrix row 39): the .NET stack has no proposal routes. Its
  approvals inbox shares the database and would list a proposal approval, but refuses to decide it.

### A shortfall above the restock limits

A blocked line can only be rejected, and an edit can only lower a quantity. So a product that needs more than
`max_restock_quantity` units, or more than `max_restock_order_value` at its cost, is proposed at the largest
order the limits allow, with the full need recorded in the line's evidence (`needed_quantity`,
`capped_by_limits`) and stated in its rationale. A product with no recorded cost is still blocked for that reason.
