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
