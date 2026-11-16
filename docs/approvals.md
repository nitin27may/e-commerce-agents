# Approvals and the Routing Policy

Some actions need a person to sign off before they happen: returns, refunds, cancelling or changing an order,
a backorder. This page describes the **routing policy** that decides *who* signs off — nobody, support, or a
manager — and the foundation it sits on. The policy engine exists and is tested on both stacks; it is **not
wired into any route, middleware or workflow yet**, so today's approval behaviour is unchanged.

## The pieces

| Piece | Where | What it does |
|---|---|---|
| Policy file | `agents/python/config/policies/approvals.yaml` | The rules, read by both stacks |
| Golden cases | `agents/python/config/policies/testdata/*.yaml` | Input facts → expected route and reasons; both stacks run every one |
| Python engine | `agents/python/shared/policy/{loader,facts,engine}.py` | Validate the file, gather facts, evaluate |
| .NET engine | `agents/dotnet/src/ECommerceAgents.Shared/Policy/ApprovalPolicy.cs` | Same file, same rules |
| Inbox schema | migration `0006_approvals_foundation.sql` | `approvals`, `approval_events` (append-only), `outbox`, `notifications` — see [Database Schema](database-schema.md) |
| Repository | `agents/python/shared/approvals/repo.py`, `shared/outbox.py` | Guarded state transitions, audit trail, outbox |

## Roles

`support` is staff and reviews what the policy routes to `support`; `admin` acts as manager and handles
`manager`. Nobody decides their own request: `decided_by` must differ from `requester_email`, enforced by a
database constraint and again in the repository. See the roles table in the [Security Guide](security-guide.md).

## The policy file

```yaml
policy_version: "2026-11-16.1"
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
