# Event autonomy

When an event arrives (a shipment running late, a price drop, a product back in stock), the system may act **on its own
inside a budget**, and must **ask a person outside it**. Python only ([parity row 40](parity-matrix.md); tracked in
[#94](https://github.com/nitin27may/e-commerce-agents/issues/94) and [#299](https://github.com/nitin27may/e-commerce-agents/issues/299)).
**Off by default** (`AUTONOMY_ENABLED=false`) and it makes **no model call** unless you also turn on the optional
`AUTONOMY_RESPONDER_ENABLED` (see [The ops-responder](#the-ops-responder)): the rules are deterministic and the customer text
is a template.

## Event types

| Topic | Action (`autonomy.yaml`) | Targets | Sized by | Money |
|---|---|---|---|---|
| `order.shipment_delayed` | `goodwill_credit` | one order | `credit_tiers`: the delay in days | store credit |
| `product.price_dropped` | `price_adjustment` | every recent **order line** of the product | `(price paid - new price) x quantity`, read from the line, never from the event | store credit |
| `inventory.back_in_stock` | `back_in_stock_notice` | every customer whose cart holds the product | not sized | none |

All three go through the same engine: tier rules, then (for money) the budget check and draw under the advisory locks, an
append-only decision row, `run_once` plus a unique `event_key`, and the same guards (a staff beneficiary is never paid alone,
the demo tour user is never acted on, the decider is never the subject). The two newer ones **fan out**: one event becomes one
decision per target, each in its own transaction under `topic:event_id:target`, so a replayed event acts once per target.

**`product.price_dropped`** (payload `product_id`, `new_price`, optional `event_id`). Targets are order lines of orders in an
eligible status, newer than `lookback_days` (30), bought for more than the new price, at most `max_lines` (200) per event; the
tour user's lines are never selected. **One adjustment per order line, ever**: a second event for a paid line is
`no_action: line_already_adjusted` and a reversal does not free the line. The engine checks it under the order lock, and a
partial unique index (migration 0017) refuses a second paid adjustment on a line even if a bug skipped the check. The order's
`per_order_max_count` (5) bounds how many of its lines may be adjusted (`no_action: order_adjustment_limit`). Rules: within 14
days and at most a 50 percent drop, an amount up to 10.00 acts alone (`auto`), up to 20.00 acts alone and tells staff
(`auto_notify`); anything else, an older purchase, a bigger drop (more likely a pricing mistake than a sale) or a missing fact,
opens an `autonomous_action` approval. A staff approval pays once for the approval and may only lower the amount.

**`inventory.back_in_stock`** (payload `product_id`, optional `event_id`). A notice, no money and no budget: it is
`auto_notify` and is bounded by the audience instead. The engine first checks stock itself (`warehouse_inventory` sum above
zero and the product active; otherwise `no_action: product_not_in_stock`, whatever the event says), then selects carts that still
hold the product and were touched within `cart_max_age_days` (30). More than `max_recipients` (200) is
`no_action: recipients_over_cap` for a person to look at (a notice has nothing for the inbox to approve). A customer gets at most
`per_customer_per_day` (2) notices a UTC day (`no_action: customer_daily_notice_cap`); the read-then-act is serialised by the same
advisory lock keyed by the action. Staff are skipped (`recipient_is_staff`), the tour user is never selected, and staff get one
heads-up per event, not one per customer. **There is no wishlist in this repository, so carts are the only audience** (the
issue also named wishlists).

**Every number is a placeholder** (the `price_adjustment` budget: 20.00 per action, 3 and 40.00 per customer per day, 5 lines per
order, 50 and 400.00 global per day; the notice limits above). Changing one means bumping `policy_version` and extending
`testdata-autonomy/cases.yaml`.

The admin simulator sends them: `{"scenario": "price-drop"}` picks the most recent eligible line (a price of 20 or more, no
decision on it yet) and drops its product 10 percent; `{"scenario": "back-in-stock"}` picks an in-stock product in a recent
customer cart that the engine has not announced; or give `{"event_type", "product_id", "new_price"}` explicitly.

## The flow

```mermaid
%%{init: {'theme':'base', 'themeVariables': {'primaryColor': '#2563eb', 'primaryTextColor': '#ffffff', 'primaryBorderColor': '#1e40af', 'lineColor': '#64748b', 'background': 'transparent'}}}%%
flowchart TD
  sim["POST /api/admin/events/simulate<br/>(the only producer today)"] --> ob["outbox row<br/>order.shipment_delayed"]
  ob --> wk["outbox worker<br/>worker/autonomy_handlers.py"]
  wk --> rule["rule: delay_days gives a credit amount<br/>autonomy.yaml events.*.credit_tiers"]
  rule --> tier["tier rules, first match wins:<br/>auto, auto_notify or needs_approval"]
  tier --> cap{"budget caps<br/>checked and drawn atomically"}
  cap -->|"fits"| act["acts alone<br/>store_credit_ledger credit<br/>agent_decisions row<br/>customer notified (auto_notify: staff too)"]
  cap -->|"autonomous tier does not fit,<br/>or needs_approval"| ask["asks<br/>approvals row (kind autonomous_action, source event)<br/>agent_decisions row (awaiting_approval)<br/>staff notified, nothing else until they decide"]
  classDef core     fill:#2563eb,stroke:#1e40af,color:#ffffff
  classDef external fill:#f59e0b,stroke:#b45309,color:#000000
  classDef success  fill:#10b981,stroke:#047857,color:#ffffff
  classDef error    fill:#ef4444,stroke:#b91c1c,color:#ffffff
  classDef infra    fill:#64748b,stroke:#334155,color:#ffffff
  class sim,ob,wk,rule,tier,cap core
  class act success
  class ask external
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

**The per-order cap has its own lock.** The day-keyed lock cannot serialise the one cap that is not a daily cap: two events for
one order that start on either side of midnight UTC hold *different* budget locks, and two approvals for one order hold none,
so both would read "this order was not paid" and both pay. The decision therefore also takes `pg_advisory_xact_lock` on the
order, and so does executing an approved credit. **Lock order, everywhere: budget, then order, then row locks** (the approval
row, then the decision row); the approved path takes no budget lock (a person authorised it, nothing is drawn) and follows the
same order from the order lock on, so the two cannot deadlock. Both races have tests with a negative control (lock removed:
both pay).

**The day key and the per-customer cap.** The per-customer cap is *per UTC calendar day*: a draw counts on, and serialises
under, the day the event started (read before the lock is awaited, so an event that waits past midnight is still booked on the
day it arrived). One customer therefore can receive a day's cap just before and again just after midnight, which is what a
calendar-day cap means; a rolling 24-hour window would be a different policy, not a different lock. The day key is right for
both daily caps; the order lock is what bounds a single order.

## The log is append-only

Migration `0012`. `agent_decisions` and `store_credit_ledger` refuse UPDATE and DELETE (trigger). What happens later is a new row:

| `entry_type` | meaning |
|---|---|
| `decision` | the engine's verdict on an event: acted alone, asked, or found nothing to do (`no_action`, with the reason). One per event (`event_key` unique) |
| `approved_execution` | staff approved an `awaiting_approval` decision and the credit ran. One per approval |
| `reversal` | an undo, with a negating ledger entry. One per reversed row |

**TRUNCATE is refused too.** A row trigger never sees `TRUNCATE`, and the seeder's `TRUNCATE ... users CASCADE` reaches both
tables through their foreign keys, so a statement-level trigger refuses any `TRUNCATE` of either table that would destroy
rows (an empty table is let through: nothing is lost, and the blanket `TRUNCATE` every test fixture runs keeps working).
The one deliberate way to clear them is the migration's `reset_autonomy_log()` function, which opens the guard for its own
transaction only. The dev/demo seeder calls it from one named function, `scripts/seed.py::reset_autonomy_log`, before its
reset, and the Python test fixture (`clean_db`) calls it between tests; no application path does. It is a dev/demo reset
and may discard history; it is not an escape hatch for production, where the append-only guarantee is the point.

The admin page derives a state from these rows (`executed`, `awaiting_approval`, `executed_after_approval`, `rejected`,
`expired`, `failed`, `reversed`, `no_action`). Reversal refuses (409) when nothing executed, it was already reversed, or the
credit has since been spent. The ledger balance is the sum of a customer's entries; **nothing spends store credit yet**.

## At most once

`run_once` (`autonomy_event:{topic:event_id}`) in front, a unique `event_key` behind it, and the decision commits on its own
connection before `run_once` marks the event complete. A redelivered row, a second outbox row for the same event, or the same
`event_id` sent twice acts once. A second event for an order already waiting on an approval is `no_action`
(`approval_already_open`); a second *new* event on an already-credited order is `no_action` with reason
`order_already_credited` (logged in the decision log, **no approval opened**): a second goodwill credit for one order is not
something the inbox should be asked, and approving it would be refused by the same ceiling. `per_order_max_count` (1 in the
shipped policy) is that ceiling for both paths, autonomous and staff-approved; the approved execution still refuses at the
ceiling as a backstop for an approval opened before the order was credited.

## Over budget: an approval

Kind `autonomous_action` in `approvals.yaml` (`amount` over 100 routes to a manager, otherwise support; no `auto` route). The
requester is the system actor `autonomy@system`, so any staff member may decide it and the "never your own request" rule
holds. Staff approve or reject in the ordinary inbox (`POST /api/approvals/{id}/decide`, source `event`):

- approving grants the credit at most once for that approval, to the order's owner; a reviewer's edit may only **lower** the
  amount (`amount`, two decimals, positive); `request_info` is refused (nobody can answer the system);
- rejecting changes nothing; the customer is never told about a credit that did not happen;
- an SLA breach escalates support to manager once, then expires it, **never while a decide holds the claim** (the timer
  waits), and an approved credit that ran but was never recorded is recorded, not expired;
- **the credit is tied to the approval and to the claim.** The credit is written in a transaction that first locks the
  approval row (`FOR UPDATE`, the same lock the SLA handler and every state change take) and pays only if the approval is
  still `pending_review` **and** the decide's own claim token still holds it. A decide whose claim went stale mid-flight
  can be overtaken by the SLA handler (which closes the approval and tells staff nothing was done); it then pays nothing and
  answers the ordinary `409`, and the reservation is released so a legitimate retry is not blocked by a cached refusal.
  `tests/test_autonomy_review_fixes.py` drives that interleaving through a real SLA tick.

## A paid credit is recorded, never undone on paper

The credit commits on its own transaction, then the approval is recorded. A request that dies in between leaves a paid
credit under a still-pending approval, so every later path must *record* it:

- **reject** answers `409` ("approve to record it") instead of recording `rejected` (checked after the claim; the credit is
  only ever written under the claim, so no approve can start paying between the check and the record);
- **approve** replays the stored `approved_execution` row directly (not through `run_once`, whose reservation a cancelled
  attempt leaves "in progress") and records the amount that went out. A retry whose amount edit differs is `409`; a retry
  without edits records `{"amount": "<paid>"}` (status `approved_with_edits` when it differs from the proposal);
- a `run_once` reservation held by another live attempt is `409`, claim handed back, nothing recorded: only a result with a
  `success` key is a verdict, so a `{"error": ...}` shape can never close the approval as `failed`;
- the SLA handler records it; `request_info` is refused for event approvals;
- an `amount` edit that is not finite or beyond decimal precision is `422`, not `500`.

Web: the decisions page pages 50 at a time (`limit`/`offset`, "Showing N of TOTAL", "Load older decisions"; the state
filter is applied in SQL, so an old waiting decision is reachable), ignores out-of-order responses (latest-request id plus
an `AbortController`), has a Refresh button, and after a demo scenario looks again every 2 s up to 5 times, stopping as
soon as the total grows.

## Nobody approves a credit to themselves

The approval's requester is the system actor, so "never your own request" alone would let a staff member approve money for
their own account. Three layers close it, and the engine never relies on the simulator being well behaved:

- **The decider is never the subject.** `authorize_decider` (Python) / `AuthorizeDecider` (.NET) refuse the reviewer when
  they are the approval's `subject_email` (case-insensitive), for every staff-decided source, again after the claim.
  Merchant proposals are the one exemption (the seller is the subject and decides on the seller routes), named in code.
  A database CHECK (`approvals_no_beneficiary_decision`, migration 0012) says the same for every source except `proposal`.
- **The engine never acts alone for a staff beneficiary** (`support` or `admin`, whoever produced the event): the verdict
  is always `needs_approval` with reason `beneficiary_is_staff`, which another staff member decides.
- **The simulator** refuses (422) an explicit order whose owner is not a `customer`, or is the acting admin.

## The demo tour user is never acted on

`agent_decisions` holds foreign keys to `users` and `orders` with no `ON DELETE` rule (the append-only trigger would refuse
`SET NULL` and `CASCADE` anyway), and the demo reset (`shared/demo_tour.py`) runs `DELETE FROM orders WHERE user_id = ...`.
One decision row referencing the tour user's order would make every later reset fail. So the engine records
`no_action: demo_user_excluded` for the tour user **without any user or order id on the row**, the scenario picker skips the
account, and an explicit simulate on its order is a 422. Every other `DELETE FROM` in application code touches cart
items, messages, checkpoints, idempotency keys, refresh tokens, embeddings or the eval seeder's products and reviews,
none of which `agent_decisions` or `store_credit_ledger` reference. The seeder's `TRUNCATE ... users CASCADE` is the
other path; see *The log is append-only*.

## Admin API and page

| Route | |
|---|---|
| `POST /api/admin/events/simulate` | `{scenario: small\|medium\|large}` or `{order_id, delay_days, event_id?}` (delay), `{scenario: price-drop}` or `{event_type: product.price_dropped, product_id, new_price}`, `{scenario: back-in-stock}` or `{event_type: inventory.back_in_stock, product_id}` (#299). 404 unless `AUTONOMY_ENABLED` **and** `AUTONOMY_SIMULATOR_ENABLED`, for every caller and *before* the role check (a 401/403 would reveal that the route exists on an install that switched it off). A scenario picks the oldest eligible order of a customer with no autonomy activity today and no event queued, so the three land on three customers. Replaying an `event_id` enqueues nothing |
| `GET /api/admin/decisions[?state=&limit=&offset=]` | decisions with state, reasons, the budget each drew on, and today's use per budget. `state` is filtered in SQL over every row (an old `awaiting_approval` decision stays findable); `limit` is at most 200, `offset` pages, `total` counts every match |
| `POST /api/admin/decisions/{id}/reverse` | admin only; works even when the feature is switched off |

Web: `/admin/decisions`.

## Booting every flag at once

`scripts/flag-smoke.sh` boots the Python stack under `LLM_PROVIDER=replay` with `AUTONOMY_ENABLED`,
`AUTONOMY_SIMULATOR_ENABLED`, `RETURN_LIFECYCLE_ENABLED`, `MERCHANT_OPS_SCHEDULE_ENABLED` and `HITL_ENABLED` on, in its own
compose project (`eca-flag-smoke`), and drives the real routes and the outbox worker with no model and no key: the three
simulator scenarios (small is an executed `auto` decision, medium an executed `auto_notify`, large waits and opens an
`autonomous_action` inbox row), a return through approval, carrier scan and inspection to `refunded`, a merchant run with its
daily timer booked, and a check that no outbox row carries an error. `--dry-run` lists the flags and checks without starting
anything; `--attach` drives a stack that is already up. CI runs it as the `flags` job in `compose-smoke.yml`.

## Gotchas

- **A flag must be passed through `docker-compose.yml` to be live in a container.** `RETURN_LIFECYCLE_ENABLED` and `MERCHANT_OPS_SCHEDULE_ENABLED` were not, so `RETURN_LIFECYCLE_ENABLED=true ./scripts/dev.sh` left the simulator route a 404 and the worker handlers off until `flag-smoke.sh` booted them together.
- **Everything is off by default.** `AUTONOMY_RESPONDER_ENABLED` is a third switch, and the only one that calls a model. `AUTONOMY_ENABLED` gates the engine and the simulator additionally needs `AUTONOMY_SIMULATOR_ENABLED`. Every number in `autonomy.yaml` is a placeholder.
- **The decision and ledger tables are append-only.** A reversal is a new row, and a reset needs `reset_autonomy_log()`: `TRUNCATE` is refused while they hold rows.
- **Lock order is budget, then order, then row locks.** Taking them in another order can deadlock two events.
- **A credited order is `no_action`.** It opens no approval.
- **.NET refuses every decision on an `event` approval** (422, "not supported on this stack").

## Not built

Still open in [#299](https://github.com/nitin27may/e-commerce-agents/issues/299): the order-risk and damaged-item events,
monthly budgets (every cap is per UTC day), and a real carrier feed (no producer exists for any event; the simulator is the only
one). The engine takes another event type by adding an action, an `events:` entry, a parser and a handler line. There is no
replay dataset for the responder: its model call is a plain chat call outside the routable agents, so it is covered by stubbed
unit tests and one recorded live validation (see the PR), not by fixtures.

## The ops-responder

`AUTONOMY_RESPONDER_ENABLED` (default `false`, needs `AUTONOMY_ENABLED`). After the engine **commits** a decision that paid a
credit (an autonomous one, or a staff-approved one), a model drafts the short customer message (an apology or an explanation)
from the decision's facts. It words a decision and cannot change one:

- **After, not during.** The engine enqueues an `autonomy.draft_requested` outbox row in the decision's own transaction (only
  when the flag is on); the worker handles it later. The ledger entry, the decision row and the customer notification already
  exist with the template text, so nothing waits on a model and a model outage loses nothing.
- **It sees only facts, and returns only text.** The prompt (`config/prompts/ops-responder.yaml`, a raw prompt, not a
  registered specialist: the orchestrator cannot route to it and no replay fixture is involved) gets a JSON object of the
  amount, order reference, delay or product and prices. Nothing it returns is read back into an amount, a tier or a state;
  `shared/autonomy/engine.py` reads `decision_drafts` only in the admin read model (a test pins that).
- **Checked against the facts** (`responder.check_draft`, built on the grounding extractor): every dollar amount must be one of
  the facts' amounts exactly (a derived sum is not accepted, unlike in chat answers), the credit amount must appear, no id, link,
  email or percentage, a day count must be the event's own, at most 450 characters.
- **Falls back to the template** on any failed check, a model error or a timeout, and stores why (`fallback_reason`).
- **Stored and shown.** One append-only `decision_drafts` row per decision (migration 0017); the admin decisions page shows the
  message and whether it is the model's or the template's; the customer's notification body is replaced with it (title and link
  unchanged), found through `notifications.decision_id`.
- Only money decisions are worded. A back-in-stock notice is not sent to a model (it could be 200 calls for one event).
- The worker container needs the chat provider variables for it; `docker-compose.yml` passes them. Cost is one short call per
  granted credit, not routed through run usage accounting.

Gotcha: because the notification is replaced after it was created, a customer who read it in the first seconds saw the template
and the next load shows the draft.

### What the TRUNCATE guard is, and is not

The triggers on `agent_decisions` and `store_credit_ledger` stop accidents: a stray `UPDATE`, `DELETE` or
`TRUNCATE` from application code, a test fixture or a console. They are not a defence against the database role the
application itself uses, which owns the tables and may call `reset_autonomy_log()`. That function is reached only from
the seeder (`scripts/seed.py::reset_autonomy_log`) and the test fixtures, never from an HTTP route. An audit log that
must survive a hostile application role needs a separate role or an external sink, which this repo does not have.
