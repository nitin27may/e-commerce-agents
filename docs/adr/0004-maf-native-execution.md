# ADR 0004 — MAF runs the tool-calling loop, not this repo

**Status:** Accepted · **Date:** 2026-08-26 (recorded; decided much earlier)

## Context

`shared/agent_host.py` originally implemented its own OpenAI chat-completions
tool-calling loop — `_run_agent_with_tools()` and `_run_agent_with_tools_stream()` —
reading tool calls off the response, dispatching them, and feeding results back.

That is a well-understood loop, and writing it by hand means owning its edge cases
forever: parallel tool calls, streaming deltas mid-call, retry, and every provider
difference.

## Decision

The hand-rolled loop was removed. Every request goes through MAF's own `agent.run()` or
`agent.run(..., stream=True)`. The `Agent` object owns its tools, system prompt and
context-provider chain; `agent_host.py` only threads the A2A request into the right call
and forwards chunks over SSE.

`CLAUDE.md` puts it in the Do Not list: *write raw OpenAI function-calling loops*.

## Why

**A framework demo that bypasses the framework demonstrates nothing.** The repo exists
to show Microsoft Agent Framework; a custom loop would have shown that MAF was not
trusted with its own core responsibility.

**The loop is where provider differences live.** Middleware, context providers, and
approval-gated tools all hook into MAF's execution. A parallel implementation would have
had to reimplement each hook or forgo it.

## Consequences

The framework's behaviour is now load-bearing, including behaviour that is surprising —
and this has been paid for in real defects:

- Agents wrapped by `AgentWorkflowBuilder` are **lazy**: without a `TurnToken` they cache
  their input and never call the model. A run missing one completes normally having done
  nothing.
- `AIFunctionFactory` serialises tool results, so what a wrapper receives is a
  `JsonElement`, not the declared `string`.
- Handoff tools are synthesised with **positional** names (`handoff_to_1`), so an agent's
  name never reaches the model and only its `description` distinguishes targets.

None of these are discoverable from a signature. Each was found by running the software.

## What would make this wrong

If MAF's loop ever blocked a requirement it could not express — a bespoke retry policy,
say, or a provider it does not support — the answer is a custom `IChatClient` or
middleware inside the framework, not a loop beside it.
