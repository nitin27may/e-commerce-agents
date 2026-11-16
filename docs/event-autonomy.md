# Event autonomy

Issue [#94](https://github.com/nitin27may/e-commerce-agents/issues/94), plan rows V-1 and V-2. When an event arrives (a
shipment running late), the system may act **on its own inside a budget**, and must **ask a person outside it**. Python
only ([parity row 40](parity-matrix.md)). **Off by default** (`AUTONOMY_ENABLED=false`) and it makes **no model call**:
the rule is deterministic and the customer text is a template.

## The flow

```
POST /api/admin/events/simulate ──► outbox row  order.shipment_delayed   (the only producer today)
                                          │   outbox worker (worker/autonomy_handlers.py)
                                          ▼
     rule: delay_days ──► a credit amount           (autonomy.yaml: events.*.credit_tiers)
     tier rules (first match wins) ──► auto | auto_notify | needs_approval
     budget caps (checked and drawn atomically) ──► an autonomous tier that does not fit becomes needs_approval
                                          │
        ┌─────────────────────────────────┴──────────────────────────────┐
        ▼ acts alone                                                      ▼ asks
  store_credit_ledger +credit                                  approvals row (kind autonomous_action, source event)
  agent_decisions row (who/what/why/budget)                    agent_decisions row (awaiting_approval)
  customer notified (auto_notify: staff too)                   staff notified; nothing else happens until they decide
```

Three delays in the shipped policy land in three tiers: 3 days is a $5.00 credit (`auto`), 6 days $15.00
(`auto_notify`: acted alone and staff are told), 12 days $40.00 (`needs_approval`, over the $25 per-action cap).

## The policy file

`agents/python/config/policies/autonomy.yaml`, validated whole at load (`shared/autonomy/policy.py`, errors are
`AutonomyPolicyError`), golden cases in `config/policies/testdata-autonomy/cases.yaml` (a separate folder: the approvals
golden cases next to it are read by the .NET tests too). Same `{fact, op, value}` shape as `approvals.yaml`.

- **A missing fact never satisfies a condition.** An unknown delay or amount can only fall through to `needs_approval`.
- **The default is to ask.** The loader refuses a policy whose last rule is not unconditional `needs_approval`, an
  autonomous rule without an `amount lte N` ceiling, or a ceiling above the budget's `per_action_max_amount`.
- **Money is `Decimal`**, at most two decimal places (more is an error, not a rounding).
- **Caps:** `per_action_max_amount`, `per_customer_per_day` (count and amount), `per_order_max_count` (ever),
  `global_per_day` (count and amount), UTC day. Exactly at a cap is allowed. A reversal does **not** give a draw back.
  A staff-approved action does not draw on the budget: a person authorised it.
- Every decision records the policy `version` it was judged by. **Every number in the file is a placeholder.**

## Atomic budgets

The whole decision runs in one transaction that first takes `pg_advisory_xact_lock(hash(budget, UTC day))`. Two events racing
for the last of a budget serialise on the lock; the second reads the first's committed draw and is turned into an approval.
`tests/test_autonomy_engine.py` proves it with a widened read-to-draw window, and includes the negative control (lock
removed: both spend). The lock is per budget and day, so one budget's events never wait on another's. Cost: all draws on one
budget serialise, which is right for a cap and fine at this scale.

## The log is append-only

Migration `0012`. `agent_decisions` and `store_credit_ledger` refuse UPDATE and DELETE (trigger). What happens later is a new row:

| `entry_type` | meaning |
|---|---|
| `decision` | the engine's verdict on an event: acted alone, asked, or found nothing to do (`no_action`, with the reason). One per event (`event_key` unique) |
| `approved_execution` | staff approved an `awaiting_approval` decision and the credit ran. One per approval |
| `reversal` | an undo, with a negating ledger entry. One per reversed row |

The admin page derives a state from these rows (`executed`, `awaiting_approval`, `executed_after_approval`, `rejected`,
`expired`, `failed`, `reversed`, `no_action`). Reversal refuses (409) when nothing executed, it was already reversed, or the
credit has since been spent. The ledger balance is the sum of a customer's entries; **nothing spends store credit yet**.

## At most once

`run_once` (`autonomy_event:{topic:event_id}`) in front, a unique `event_key` behind it, and the decision commits on its own
connection before `run_once` marks the event complete. A redelivered row, a second outbox row for the same event, or the same
`event_id` sent twice acts once. A second event for an order already waiting on an approval is `no_action`
(`approval_already_open`); a second *new* event on an already-credited order asks a person (`budget:order_count`).

## Over budget: an approval

Kind `autonomous_action` in `approvals.yaml` (`amount` over 100 routes to a manager, otherwise support; no `auto` route). The
requester is the system actor `autonomy@system`, so any staff member may decide it and the "never your own request" rule
holds. Staff approve or reject in the ordinary inbox (`POST /api/approvals/{id}/decide`, source `event`):

- approving grants the credit at most once for that approval, to the order's owner; a reviewer's edit may only **lower** the
  amount (`amount`, two decimals, positive); `request_info` is refused (nobody can answer the system);
- rejecting changes nothing; the customer is never told about a credit that did not happen;
- an SLA breach escalates support to manager once, then expires it, **never while a decide holds the claim** (the timer
  waits), and an approved credit that ran but was never recorded is recorded, not expired.

## Admin API and page

| Route | |
|---|---|
| `POST /api/admin/events/simulate` | `{scenario: small\|medium\|large}` or `{order_id, delay_days, event_id?}`. 404 unless `AUTONOMY_ENABLED` **and** `AUTONOMY_SIMULATOR_ENABLED`. A scenario picks the oldest eligible order of a customer with no autonomy activity today and no event queued, so the three land on three customers. Replaying an `event_id` enqueues nothing |
| `GET /api/admin/decisions[?state=]` | decisions with state, reasons, the budget each drew on, and today's use per budget |
| `POST /api/admin/decisions/{id}/reverse` | admin only; works even when the feature is switched off |

Web: `/admin/decisions`.

## Not built

The LLM `ops-responder` mode (`EVENT_AGENT_MODE=agent`) and its fixtures need a keyed recording session. Price-drop,
back-in-stock and order-risk events are cut by the plan (consent UX, checkout hot path); the engine takes another event type
by adding an `events:` entry, a parser and a handler line. No real carrier feed produces `order.shipment_delayed`.
