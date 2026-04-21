# Refactor 07 — Context Providers Cleanup

## Goal

Split `agents/shared/context_providers.py:17` `ECommerceContextProvider` into three composable providers so specialists opt in only to what they need.

## Deliverables

- New providers:
  - `UserProfileProvider` — injects `state["user"]` (name, email, role).
  - `RecentOrdersProvider` — injects `state["recent_orders"]` (last N orders, configurable).
  - `AgentMemoriesProvider` — injects `state["memories"]` from the `agent_memories` table.
- Each specialist's `agent.py` composes only the providers it actually uses.
- Remove free-form text injection into system prompts; use `state` consistently.
- MAF v1 return-type compliance.

## Test file

`agents/tests/test_context_providers.py` — minimum 6 tests:

- Each provider injects exactly the expected state keys.
- Chain of three providers merges state without collision.
- DB failure in one provider degrades gracefully (others still run).
- Removed: old monolithic provider is not referenced anywhere (grep check).
- Specialists that don't need recent orders don't pay the cost of fetching them.
- Role-specific behavior preserved (admin vs customer).

## Verification

- Canned queries return the same grounded answers before and after (parity test).
- `pytest agents/tests/test_context_providers.py` green with coverage ≥ 80% on the file.

## Out of scope

- Provider caching (noted as follow-up; currently rebuilds state per run).
