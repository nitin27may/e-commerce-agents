# Chapter 15 — Group Chat Orchestration

## Goal

Teach `GroupChatBuilder` — a centralized manager selects which agent speaks next from a pool.

## Article mapping

- **New chapter** (not covered in old series)
- **New slug**: `/posts/maf-v1-group-chat-orchestration/`

## Teaching strategy

- [x] New mini-example — `Writer` + `Reviewer` iterate on marketing copy; `Manager` decides when the work is done.

## Deliverables

### `python/`
- `main.py` — round-robin manager for 2 turns, then a prompt-driven manager that decides iteration count.
- `tests/test_group_chat.py` — ≥ 4 tests: round-robin yields expected speaker order; manager termination respected; full history visible to all participants; custom manager callable.

### `dotnet/`
- `CreateGroupChatBuilderWith(RoundRobinGroupChatManager)` plus a prompt-driven manager.

### Article
- Speaker selection strategies (round-robin vs prompt-driven vs agent-driven).
- Iterative refinement patterns.

## Verification

- Writer drafts copy; Reviewer critiques; Writer revises; Manager ends iteration after N rounds.

## How this maps into the capstone

Future feature candidate: a "product launch review" flow combining Product Discovery, Pricing, Legal specialists under a manager agent. Not landed in the initial Phase 7 refactor; noted as a follow-up.

## Out of scope

- Magentic (Ch16) — dynamically selects which agent to even involve.
