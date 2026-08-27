# ADR 0005 — Two backends, one frontend, gated by a real test run

**Status:** Accepted · **Date:** 2026-08-26 (recorded; decided much earlier)

## Context

Microsoft Agent Framework ships a Python SDK and a .NET SDK. Most samples pick one. This
repo implements the same domain twice — `agents/python/` and `agents/dotnet/` — behind a
single Next.js frontend that switches with `NEXT_PUBLIC_BACKEND_STACK`.

Two implementations of one domain is the most expensive shape this project could have
chosen, so the reason has to be worth it.

## Decision

Both stacks are maintained. Python is the reference implementation and lands features
first; .NET follows on a prioritised backlog rather than in lockstep. Parity is defined
by **`web/e2e/orchestration-parity.spec.ts` passing against both**, not by a matrix row.

## Why

**It is the differentiator.** Nobody else has the same domain, the same frontend and the
same protocol implemented in both SDKs, with the differences written down.

**A shared protocol is what makes it possible.** Because specialists speak A2A over HTTP
([ADR 0001](0001-a2a-over-direct-calls.md)) and prompts live in one YAML corpus
([ADR 0003](0003-yaml-prompt-composition.md)), the two stacks share a contract rather
than a codebase.

**It surfaces framework differences honestly.** `docs/parity-matrix.md` lists what still
differs and why, including decisions not to port — the .NET stack deliberately reuses the
Python seeder and auth-server, because a second seeder would have to produce
byte-identical rows or the two stacks would diverge in catalogue content.

## Consequences

**Parity claims are worthless without a run.** The repo's 109-test e2e suite passed green
against a .NET backend missing four whole features, because it never exercised them. That
is why `parity-gaps.ts` exists and why every assertion in the parity spec checks for
*presence*: a test that only confirms "no error appeared" goes green against a blank page.

**The gate is not in CI.** `tests.yml` says so explicitly — it needs a full stack and a
real API key, which is impractical per push. The cost is not theoretical: the .NET
orchestrator could not answer a single question for an extended period, and unit tests,
container health checks and image builds were all green throughout. It was found by
running the browser suite by hand.

That is the standing weakness of this decision, and it is recorded rather than resolved.

## What would make this wrong

If the .NET backlog stopped moving, this would become one working stack and one
half-finished one — worse than having picked a single SDK, because the parity matrix
would be advertising a promise nobody was keeping.
