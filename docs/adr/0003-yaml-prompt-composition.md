# ADR 0003 — Prompts compose from YAML, never hardcoded strings

**Status:** Accepted · **Date:** 2026-08-26 (recorded; decided much earlier)

## Context

Six agents need system prompts. Several share the same grounding rules, the same schema
context, and the same tool examples. Written as Python string literals, those shared
parts get copy-pasted and then drift.

## Decision

Prompts live in `agents/python/config/prompts/{agent}.yaml` and are **composed** at
agent-construction time by `shared/prompt_loader.py`: base prompt + shared grounding
rules + role-specific instructions + schema context + tool examples. Shared fragments
live in `config/prompts/_shared/`.

`CLAUDE.md` states the rule directly: *do not hardcode prompts in Python*.

## Why

**One corpus, two stacks.** The .NET Dockerfiles copy `agents/python/config` verbatim,
and .NET's `PromptLoader` reads the same files. A prompt fix reaches both backends at
once, and neither can drift from the other.

**Role-awareness has to be per-request.** `get_system_prompt(current_user_role.get())`
runs when the agent is built, and agents are rebuilt per request — so an admin genuinely
sees different instructions from a customer. An earlier revision built the prompt once at
*import* time with a hardcoded default role, which silently defeated every role-specific
block in the YAML. That bug is the strongest argument for this decision.

## Consequences

The corpus is a **cross-stack API**, not documentation — and that is easy to forget.

It has already bitten twice, both times in the .NET stack:

- Prompts name tools the way Python declares them (`call_specialist_agent`,
  `get_order_details`). .NET registered the C# spellings, so the model was told one name
  and offered another on every turn. Thirty-nine of forty-six tools were affected; the
  orchestrator's was fatal.
- `handoff` mode reused `orchestrator.yaml`, which instructs the model to route via
  `call_specialist_agent` — the *tool router's* mechanism. In a handoff mesh that meant
  the agent never handed off, and autonomous mode looped it into a 23,000-character
  monologue.

Both were prompt/contract mismatches, not code bugs, and neither could fail a build.

## What would make this wrong

Nothing about the composition; the coupling is the point. But if the two stacks ever
diverged enough to need genuinely different prompts, per-stack overrides would be needed —
and the shared-corpus guarantee would have to be replaced by something that still fails
loudly when they disagree.
