# Approvals and the Routing Policy

Some actions need a person to sign off before they happen: returns, refunds, cancelling or changing an order,
a backorder. This page describes the **routing policy** that decides *who* signs off (nobody, support, or a
manager), the inbox and worker behind it, and the **approvals API** staff use to decide. It is for anyone
changing the policy, calling the API or reading how a decision is executed.

Every gated tool call and every paused return opens an inbox `approvals` row routed by the policy, and every
approval is decided through one function (see [The approvals API](#the-approvals-api)). Both stacks serve the
same routes with the same JSON over the same tables, so the web UI calls one API whichever orchestrator is
behind it. The gaps are listed under [What is not built](#what-is-not-built).

## The pieces

| Piece | Where | What it does |
|---|---|---|
| Policy file | `agents/python/config/policies/approvals.yaml` | The rules, read by both stacks |
| Golden cases | `agents/python/config/policies/testdata/*.yaml` | Input facts → expected route and reasons; both stacks run every one |
| Python engine | `agents/python/shared/policy/{loader,facts,engine}.py` | Validate the file, gather facts, evaluate |
| .NET engine | `agents/dotnet/src/ECommerceAgents.Shared/Policy/ApprovalPolicy.cs` | Same file, same rules |
| Inbox schema | migration `0006_approvals_foundation.sql` | `approvals`, `approval_events` (append-only), `outbox`, `notifications` — see [Database Schema](database-schema.md) |
| Repository | `agents/python/shared/approvals/repo.py`, `shared/outbox.py` | Guarded state transitions, audit trail, outbox |

## How a gated tool call reaches the inbox (Python)

The gated tools (`cancel_order`, `modify_order`, `initiate_return`, `process_refund`, `place_backorder`) are declared
`@tool(approval_mode="always_require")`. MAF acts on that setting before it builds the function-middleware
pipeline: the run ends with a `function_approval_request` in `response.user_input_requests`, the tool does not run,
and the response text is empty. `HITLFunctionMiddleware` therefore never sees these calls on the real path (it
still guards an ungated registration of the same names). The specialist host closes the gap:

* `shared/agent_host.py::park_pending_approvals` runs after every blocking and streaming agent run (the legacy
  `/message:send` and `/message:stream` routes and the A2A 1.0 executor share those two functions).
* For each parked call it calls `shared/hitl.py::park_gated_call`, which writes the `tool_approval_requests` row and
  the routed inbox `approvals` row, owned by the forwarded user. The tool is not executed and no model call is made.
* It is idempotent: the same user asking for the same tool with the same input while a request is `pending` or
  `processing` gets that request back (one row, one inbox entry), also under concurrent turns.
* The user reads a fixed sentence, for example "I have asked a team member to approve cancelling order <id> (ref:
  ab804829). Nothing has changed yet; you will be notified once it has been reviewed." With no forwarded identity
  or a database error the reply says nothing was changed and no row is written (fail closed).
* In `tool` mode the orchestrator relays that sentence; the in-chat approval card exists only for the workflow
  modes. Staff see the request in the inbox either way.

## Roles

`support` is staff and reviews what the policy routes to `support`; `admin` acts as manager and handles
`manager`. Nobody decides their own request: `decided_by` must differ from `requester_email`, enforced by a
database constraint and again in the repository. See the roles table in the [Security Guide](security-guide.md).

## The policy file

```yaml
policy_version: "2026-11-16.3"
kinds:
  return_approval:
    editable_fields: [amount, resolution]
    sla_hours: 24
    on_timeout: expire
    rules:
      - id: outside-return-window
        route: manager
        reason: Delivered more than 30 days ago, outside the return window
        when:
          - { fact: days_since_delivery, op: gt, value: 30 }
      # ... more rules ...
      - id: default-support
        route: support
        reason: Needs a support review
        when: []
```

* **Ordered, first match wins.** A rule matches when *all* its `when` conditions hold. The last rule of a kind
  has an empty `when`, so a route is always found. An unconditional rule anywhere but last is rejected, because
  it would make the rest unreachable.
* **No expressions.** A condition is `{fact, op, value}`. Operators are `lt`, `lte`, `gt`, `gte`, `eq` and `in`
  (`in` takes a list). Nothing is evaluated from a string.
* **Facts**: `amount`, `days_since_delivery`, `loyalty_tier` (`bronze`/`silver`/`gold`), `returns_90d`,
  `refund_to_spend_ratio` (`amount` ÷ lifetime spend) and `account_age_days`.
* **Unknown facts never match.** A fact that could not be determined (the order is not delivered yet, no spend
  is recorded) makes any condition on it false, including `lt`. So an `auto` rule can never fire on missing
  evidence — it falls through to `support`.
* **Per kind**: `editable_fields` (what a reviewer may change when approving with edits), `sla_hours` (how long
  a review may wait before it is reminded and escalated) and `on_timeout` (`reject` or `expire`).
* **Loading fails loudly.** An unknown key, fact, operator, route, loyalty tier or timeout action, a value of
  the wrong type, a duplicate rule id or a kind with no catch-all rule is an error at load time, not at the first
  approval that hits it.

### The default policy

Kinds: `return_approval` plus the gated tools in `shared/hitl.py` (`initiate_return`, `process_refund`,
`cancel_order`, `modify_order`, `place_backorder`). The three money kinds share one rule list.

| Order | Rule | Route |
|---|---|---|
| 1 | Delivered more than 30 days ago | manager |
| 2 | 3 or more returns in 90 days | manager |
| 3 | Refund over half of lifetime spend | manager |
| 4 | Amount over 500 | manager |
| 5 | Account under 30 days old and amount over 100 | manager |
| 6 | Amount ≤ 50, in window, fewer than 2 returns, ratio ≤ 0.25, account ≥ 30 days | **auto** |
| 7 | Amount ≤ 150, in window, silver or gold, fewer than 2 returns, ratio ≤ 0.25, account ≥ 30 days | **auto** |
| 8 | Everything else | support |

`cancel_order`: over 1000 → manager; up to 100 with fewer than 3 returns → auto; else support. `modify_order`:
over 1000 → manager, else support. `place_backorder`: always manager.

### Changing the policy

1. Edit `approvals.yaml` and bump `policy_version` (it is stored on every approval it routes).
2. Add or adjust golden cases in `testdata/`. Every rule must be hit by at least one case; both test suites
   enforce it.
3. Run both: `cd agents/python && uv run pytest tests/test_policy_engine.py` and
   `cd agents/dotnet && dotnet test tests/ECommerceAgents.Shared.Tests --filter ApprovalPolicyTests`.

The two engines read the same file and run the same cases, so a change that behaves differently on one stack
fails there. One known gap: Python rejects an unquoted `policy_version` that YAML would read as a number
(`2026.11`); .NET reads it as text. Always quote it.

## Using the engine (Python)

```python
from shared.policy import evaluate
from shared.policy.facts import gather_facts

facts = await gather_facts(order_id, amount=refund_amount)  # who is asking: the current_user_email ContextVar
decision = evaluate("return_approval", facts)
decision.route, decision.reasons, decision.evidence, decision.version
```

`gather_facts` reads who is asking from the request's identity and scopes every query to that person; for a
stored approval (a worker has no request context) pass `subject_email=` explicitly. `evaluate` is pure: same
facts, same decision, and `evidence` records the rule, its conditions and the facts it saw.

## The outbox worker, SLA and notifications

A review that nobody picks up must not sit forever. `sla_hours` and `on_timeout` in the policy file drive that,
and a small worker process enforces it.

**Scheduling.** When an approval enters `pending_review` — created there, or moved there (from `pending_confirm`,
or back from `needs_info`) — the repository (`shared/approvals/repo.py`) sets `due_at = now + sla_hours` and enqueues
two outbox rows in the *same transaction*: `approval.reminder_due` at `APPROVAL_REMINDER_FRACTION` of the way
to the deadline and `approval.sla_breached` at the deadline. A timer is just an outbox row whose `available_at`
is in the future. Scheduling lives in the repository, not in a route, so every creator gets it for free and a
rolled-back approval leaves no timer. A kind missing from the policy file gets no deadline (and a warning).

**What the handlers do** (`worker/approval_handlers.py`):

| Event | Effect |
|---|---|
| `approval.reminder_due` | A notification to the assigned role, once (audit event `sla_reminder_sent`) |
| `approval.sla_breached`, assigned to `support` | Escalate once to `manager` (the `admin` role): `assigned_role`, `escalation_level + 1`, a fresh deadline and timers, audit event `escalated`, a notification to managers |
| `approval.sla_breached`, at the top level | Apply the kind's `on_timeout` (`reject` or `expire`) through a guarded transition, audit event `sla_timeout`, and tell the requester |

Handlers lock the approval and ignore the event unless it is still a `pending_review` at the same escalation level
and deadline the timer was set for. So a redelivered event, an approval that was decided meanwhile, one that went
to `needs_info`, and a timer left from an earlier trip through review are all no-ops. State changes go through
`repo.transition()` / `repo.escalate()`, never a bare UPDATE.

**The worker** (`worker/`): an asyncio loop over `outbox` using `SELECT ... FOR UPDATE SKIP LOCKED`, so several
workers can run safely. Each row runs in its own transaction with its handler's writes. Claiming leases the row
(`locked_until`, `locked_by`) and counts the attempt; if the worker dies, the lease runs out and another worker
takes the row (a worker that lost its lease cannot also finish it). A failing handler rolls back, the row records a
fixed error class (`handler_error`, `database_error`, `timeout`, `unknown_topic` — never payload text), and is retried
with capped exponential backoff. After `OUTBOX_MAX_ATTEMPTS` the row is **parked** (`failed_at` set, never retried);
admins list them at `GET /api/admin/outbox/parked`. SIGTERM finishes the current row, hands the rest back and exits.
One Python worker serves both stacks, which share the database.

**Notifications.** The worker writes `notifications`, addressed to a person or to a role ("all support staff").
`GET /api/me/notifications` returns the caller's own plus those addressed to the caller's role, newest first, with the
unread count; `POST /api/me/notifications/{id}/read` marks one read for the caller only (404 for someone else's).
The web sidebar shows a bell with an unread badge and a short list, polling every 30 seconds.

**Run it locally.**

```bash
cd agents/python && uv run python -m worker.main        # needs DATABASE_URL and a migrated database
docker compose --profile agents up outbox-worker        # or as part of the stack
```

## Merchant limits

[Merchant proposals](merchant-proposals.md) (Python only) judge every proposed line against these limits, when
it is generated (`shared/merchant/proposals.py`) and again when it is applied (`shared/merchant/execute.py`).

`agents/python/config/policies/merchant_limits.yaml` holds the hard limits, separate from the approval
routing policy above. A limit is *hard*: a line that breaks one is marked `blocked` with a reason code, and no
approver can wave it through.

| Key | Meaning |
|---|---|
| `margin_floor_pct` | Lowest margin a price may leave, `(price − unit_cost) / price × 100`. Exactly at the floor is allowed; a product with no `unit_cost` is blocked. |
| `max_change_pct_per_day` | Most a price may move in one UTC day. Every change already in `price_history` today counts, plus the proposed one, each as `abs(new − old) / old × 100`. Exactly at the cap is allowed. |
| `block_during_active_promotion` | Refuse a price change to a product covered by a promotion active now (`promotion_products`, `promotions.is_active`, start and end dates). |
| `max_lines_per_batch` | Most lines one proposal batch may hold. |
| `max_restock_quantity` | Most units one restock line may order (1 to INT32). **Placeholder** default 5000. |
| `max_restock_order_value` | Most one restock line may commit, `quantity × unit_cost`. **Placeholder** default 50000. |
| `policy_version` | Recorded on every guard result. Bump it when a number changes. |

`shared/merchant/guards.py` is pure: `check_price_change`, `check_restock_line` and `check_batch` take the
proposed change plus the facts and return a `GuardResult` (`ok`, the `reasons`, the `numbers` used and the
`policy_version`). Every applicable reason is reported, not just the first. The reason codes are fixed:
`invalid_price`, `missing_cost`, `margin_below_floor`, `daily_change_cap_exceeded`, `active_promotion`,
`invalid_quantity`, `batch_too_large`, `restock_quantity_exceeded`, `restock_value_exceeded`, and three raised when a line is applied: `edit_beyond_proposal` (a reviewer's value raises the line: a restock above the proposed quantity, a price deeper than proposed or above where it started), `price_moved` (the price is no longer the one the line was proposed against) and `restock_already_scheduled` (stock is already on order). `shared/merchant/facts.py` reads the facts from Postgres (current price,
`products.unit_cost`, today's `price_history` steps, the promotions linked in `promotion_products`), and
`shared/merchant/limits.py` validates the YAML at load time, failing loudly on an unknown key or out-of-range
value.

**A merchant proposal is not the staff inbox's to decide, on both stacks.** Every staff route answers **404 `Approval not found`** for source `proposal`, byte-for-byte what a missing id gets, so staff cannot tell a proposal id from an unused one: `decide()` and the answer-info route (for every decision, including `request_info`: a batch parked in `needs_info` could never be answered by its system-actor requester) and `GET /api/approvals/{id}`, matching the staff list that leaves it out (.NET's list excludes it too); the legacy resume and admin approve/deny routes cannot reach one. The seller decides on `/api/seller/proposals/{id}/decide` (Python only). An SLA breach never expires a proposal under a decide in flight: see [merchant proposals](merchant-proposals.md#sla-expiry-and-a-decide-in-flight).

The schema is migration `0009_merchant_proposals.sql`: `proposals`, `proposal_lines` (per-line status
including `blocked`, the guard result as JSONB), `promotion_products` and a nullable `products.unit_cost`. The
seeder fills `unit_cost` from the price by a per-category ratio and links the bundle and flash-sale promotions
to their products, in a step appended after every other one so no previously seeded value moves.

## The approvals API

On Python all of it lives in `orchestrator/routes/approvals.py`, every rule in `orchestrator/approvals_service.py`. On
.NET the routes are `Routes/ApprovalRoutes.cs` and the rules are `Approvals/ApprovalsService.cs`
(see [The .NET stack](#the-net-stack)); the paths, JSON field names, status codes and `{"detail": ...}` error bodies are
the same.

| Route | Who | What |
|---|---|---|
| `GET /api/approvals` | staff | The queue for the caller's role (open items by default; `?status=all` or one status). `support` sees what is routed to `support`; `admin` sees everything |
| `GET /api/approvals/{id}` | staff, at or above the assigned role | The approval, its audit events, the policy evidence, the fields a reviewer may edit, and what it would execute |
| `POST /api/approvals/{id}/decide` | staff | `{decision: approve \| reject \| request_info, edits?, note?}` |
| `POST /api/approvals/{id}/info` | the requester only | `{message}` — answers a request for more information |
| `GET /api/me/approvals` | any signed-in user | Their own approvals, status only |

**Who may decide.** Only staff; never the requester (compared case-insensitively, even for an admin deciding their
own request); **never the subject** (the party who receives the money or benefit: an autonomous action's requester is
the system actor, so a staff member whose own order a credit is for must not be able to approve it; `403`, every source
except `proposal`, whose seller *is* the subject and the intended reviewer and decides on the seller routes, never
through this check: `SELLER_DECIDED_SOURCES` in Python, `SellerDecidedSources` in .NET, named rather than silently
exempt; a database CHECK, `approvals_no_beneficiary_decision` in migration 0012, backs it with the same exemption); and the staff role must be at or above the approval's `assigned_role` — `support` decides what the
policy routed to `support` (and `auto` requests still waiting for a person), `admin` decides everything including
`manager` routes. A refusal is a `403` and changes nothing; it is checked before anything is claimed, **and again
after the claim is taken**, on every path (approve, reject and request-info; tool and workflow alike). The first check
ran on an earlier read, and an SLA escalation (`support` to `manager`) can land between that read and the claim; the
claim is what closes the window (`escalate` refuses while one is held), so the fresh row is reloaded and the same
decider check re-run, the claim is handed back and the normal `403` returned if it now fails. Role rank is
case-insensitive on both stacks, and a route the policy engine returns that is not `auto` / `support` / `manager`
fails closed to a manager (logged) on both.

| Caller | support route | manager route |
|---|---|---|
| The requester (any role) | 403 | 403 |
| Another customer or a seller | 403 | 403 |
| `support` | allowed | 403 |
| `admin` | allowed | allowed |

**Deciding executes the action exactly once.**

* *Tool approvals* take a TTL'd claim on the approval (`repo.claim`), run `execute_approved_action` keyed by the
  **approval id** — a reviewer's edits change the tool input, so an input hash would let an edited retry run twice —
  then record the outcome fenced by the same claim token. A second concurrent decide gets `409`.
* *Workflow approvals* resume the paused workflow from its checkpoint (`resume_workflow`): an atomic
  `pending -> processing` claim on the `hitl_requests` row fenced by `claimed_at`, a hard timeout inside the claim TTL,
  and the remaining steps executed **as the requester** with the reviewer recorded separately.
* A decision that fails to execute is **not recorded**: the claim is handed back, the approval stays in
  `pending_review`, and the reviewer can try again.
* **An action that ran but whose recording lost the claim is finished, not abandoned.** If the executor ran and the
  request row is `executed` (carrying this approval's id and result) but the approval was left in `pending_review`
  (the claim was taken over before the outcome was recorded), a retry of *approve* records the replayed result and moves
  the approval to `executed`/`failed` instead of answering `409` forever, and the timeout handler (`expire`, which also
  serves .NET) does the same instead of marking an executed thing `expired`. *Request information* on such a row is
  refused (`409`): the action already happened. A request that is resolved any other way is still a `409`.
* **The same holds for an autonomous-action (`event`) credit, and a reject never contradicts a paid credit.** The credit
  commits on its own transaction before the approval is recorded. If it ran and the recording died, *reject* answers
  `409` ("approve to record it") rather than recording `rejected` while the customer keeps the money; *approve* replays
  the stored ledger row (it does not go back through `run_once`, whose reservation a cancelled attempt leaves "in
  progress") and records the amount that **actually went out**: a retry's conflicting amount edit is `409`, and a retry
  without edits records `{"amount": "<paid>"}`. A `run_once` reservation held by another live attempt is `409` with the
  claim handed back, never a `failed` approval. The SLA handler records such a credit instead of expiring it. Tool and
  workflow approvals are safe the same way: their reject is a `409` once the request row is `executed`/resolved.
* An `amount` edit that is not a finite number a database column could hold (`1e30`, a 400-digit string, NaN, an integer
  past float range) is a `422`, never a `500`.
* A claim lost while recording a *workflow* decision is a `409` ("taken over by another reviewer"), like every other
  lost claim, on both stacks.

**Edits** (`approve` only) are merged only for fields on the policy's `editable_fields` for that kind; any other
field, or a bad value, is a `422` and nothing runs. For a tool approval they merge into the tool input; for a return
they are `amount` (not above what is under review) and `resolution` (`refund`, `store_credit` or `replacement`), and
are honoured when the return is created.

### The return workflow

`workflow:return-replace` is `check-eligibility → policy-gate → initiate-return → search-replacements →
apply-discount → finalize`. The gate asks the policy engine (kind `return_approval`): `auto` proceeds without
pausing; anything else pauses via `ctx.request_info` and opens an approval. **Nothing is created before the gate** —
the `returns` row, the label and the order's `returned` status all come from `initiate-return`, which runs only after
`auto` or a reviewer's approval. So a rejected or expired request leaves no return and the order untouched.

The pause carries the whole `WorkflowState` and is answered with a `ReturnDecision` (`decision`, `resolution`,
`amount`, `note`, `decided_by`, `approval_id`), so the resumed run restores everything and runs as the requester.
`RETURN_HITL_THRESHOLD` is only the fallback when the engine cannot decide. Migration `0010` expires returns paused
before the policy gate existed: their checkpoints cannot be resumed with the `ReturnDecision` response type.

**Request more information** does not touch MAF. The approval moves to `needs_info`, the reviewer's question is
written into the requester's conversation, and a paused workflow stays paused (its `hitl_requests` row is parked as
`needs_info`, so it cannot be resumed meanwhile). The requester's answer (`/info`) moves it back to
`pending_review`. A reviewer may still reject while it waits.

**Expiry.** At the top escalation level the worker does not just flip the row: it queues `approval.timeout_apply`,
which calls `orchestrator.approvals_service.expire(id)` — it resumes a paused workflow as a rejection (nothing is
created) or denies a tool request, then marks the approval `expired` (or `rejected` for `on_timeout: reject`). It runs
as its own outbox row because resuming a workflow cannot happen inside the breach row's locked transaction. An `auto`
route (recorded, waiting for a person, assigned to support) gets the same SLA timers: reminded, escalated to a manager,
then timed out.

### The legacy endpoints

`POST /api/orchestration/{run_id}/resume` and `POST /api/admin/hitl/requests/{id}/approve|deny` are thin shims: they
find (or, for a request that predates the inbox, create) the linked approval and call the same `decide`. They are
staff-only, bounded by the assigned role, and keep their own status codes. There is one code path.

## What is not built

* *Customer confirm* (`pending_confirm`), on either stack.
* *Acting on an `auto` route for tool calls.* The route is recorded, and the request still waits for a person.
* *A .NET SLA worker.* The Python worker serves both stacks; see [The .NET stack](#the-net-stack) for the one case
  it cannot finish.

## The .NET stack

The .NET orchestrator serves the same API over the same tables (`approvals`, `approval_events`, `outbox` — migrations
`0006`+; .NET adds no schema). It is a port, so the rules above hold unchanged and each has a test in `agents/dotnet/tests/`:

| Piece | Where |
|---|---|
| Routes | `Orchestrator/Routes/ApprovalRoutes.cs` — `/api/approvals`, `/api/approvals/{id}`, `/decide`, `/info`, `/api/me/approvals` |
| Decide path | `Orchestrator/Approvals/ApprovalsService.cs` — authorisation, claims, edits, executing the paused work. The legacy `POST /api/orchestration/{run_id}/resume` and `POST /api/admin/hitl/requests/{id}/approve\|deny` are shims over it, and are staff routes bounded by the assigned role, as on Python |
| Repository | `Shared/Approvals/ApprovalRepository.cs` — create (idempotent per source), guarded `TransitionAsync`, `ClaimAsync` / `ReleaseClaimAsync` with a fencing token and TTL, `EscalateAsync` |
| Timers | `ApprovalRepository` writes `approval.reminder_due` and `approval.sla_breached` in the same transaction as the state change (`Shared/Approvals/Outbox.cs`) |
| Intake | `Shared/Approvals/ApprovalIntake.cs` — routes a gated tool call (`HitlGate`) or a paused return (`ChatRoutes`) through the policy and opens the row; the policy gate's own decision routes a return, not a second evaluation |
| Executor | `Orchestrator/Routes/HitlActionExecutor.cs`, run once per approval id through `IdempotencyGuard.RunOnceAsync` |

**One worker for both stacks, bar one topic.** The Python worker drains timers .NET wrote (the exception is `approval.timeout_apply` for a .NET return, below). The timer's dedupe key
(`{topic}:{approval id}:{level}:{epoch ms of due_at}`), its payload (`approval_id`, `level`, `due_at`) and the way `due_at` is
written (microsecond precision, `+00:00`) are what the worker's handlers compare, so they match what Python writes.
`ApprovalCrossStackTests` dumps what .NET writes from Postgres and compares it to
`agents/python/tests/data/dotnet_approval_rows.json`; `test_approval_dotnet_rows.py` loads that same file and runs the real
worker handlers over it (reminder, escalation, a stale timer). Change the .NET write path and the .NET test fails until the
fixture is regenerated (set `APPROVAL_DUMP_PATH` to the fixture's path and run `dotnet test --filter ApprovalCrossStackTests`);
the Python test then proves the worker still reads it.

**Known differences from Python:**

* *An expiry of a paused .NET return is applied by .NET, not by the Python worker* (parity row 36). Both stacks consume
  `approval.timeout_apply`, and only a stack can resume its own checkpoint (a .NET workflow's checkpoint is MAF .NET's JSON,
  `PostgresJsonCheckpointStore`, which Python's storage cannot decode). So `ApprovalIntake` tags every paused-return approval a
  .NET orchestrator opens with `recommendation.stack = "dotnet"` (no schema change), the Python worker's claim query skips
  `approval.timeout_apply` rows for a `workflow` approval carrying that tag, and the .NET `ApprovalTimeoutWorker` hosted service
  claims only those (`FOR UPDATE SKIP LOCKED`, lease, capped backoff, parked after `OUTBOX_MAX_ATTEMPTS`). It calls
  `ApprovalsService.ExpireAsync`: the approval's claim is taken first (a decide in flight wins, a lost claim is a 409 skip), the
  run is resumed as a rejection as the requester with the reviewer recorded as `system`, and the approval ends `expired` (or
  `rejected` for `on_timeout: reject`). A run an approver already resumed but never recorded is recorded, not run again.
  `DOTNET_TIMEOUT_WORKER_ENABLED=false` stops it, and those rows then wait. An approval opened before the tag existed has none,
  so it still goes to Python and is retried and parked as it always was; a manager's decision closes it. A .NET *tool* approval
  needs no tag: its expiry only denies the request row, which either worker can do.
* *Requests stored before that change* (PascalCase tool name, camelCase input keys) are mapped when approved: the name
  as before, and the top-level input keys with the same snake_case rule (`orderId` becomes `order_id`; nested objects are
  left alone). If two different keys would collapse onto one name with different values the mapping is a guess, so the
  approve is refused with `422` before anything is claimed or executed, the approval stays `pending_review` (it can still
  be rejected), and a `legacy_input_refused` event is written to its audit trail once.
* *A gated .NET tool call is stored with the same name and input keys as Python* (`cancel_order`, `order_id`), not the .NET
  pipeline's `CancelOrder` / `orderId` — that is what the policy kinds, the executor and the other stack key on.
* *A stored request in the .NET spelling* (PascalCase tool name, camelCase input keys) is mapped when approved: the name
  and the top-level input keys to snake_case (`orderId` becomes `order_id`; nested objects are left alone). If two
  different keys would collapse onto one name with different values the mapping is a guess. The approve is then refused
  with `422` before anything is claimed or executed, the approval stays `pending_review` (it can still be rejected), and
  a `legacy_input_refused` event is written to its audit trail once.
* *`/api/me/notifications`* has no .NET route (see the parity matrix).
