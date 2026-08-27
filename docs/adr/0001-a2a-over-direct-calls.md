# ADR 0001 — Specialists talk over A2A HTTP, not in-process calls

**Status:** Accepted · **Date:** 2026-08-26 (recorded; decided much earlier)

## Context

Six agents share one domain. The orchestrator has to reach five specialists, and the
obvious implementation is a Python function call: they are all in the same repository
and could trivially be in the same process.

They are not. Each specialist is an independent service behind an
[A2A](https://a2aproject.github.io/A2A/) endpoint (`POST /message:send`,
`POST /message:stream`, `GET /.well-known/agent-card.json`), reached over HTTP even
when both ends are containers on the same Docker network.

## Decision

Specialists are separate services and every orchestrator→specialist hop is an A2A HTTP
call.

## Why

**It is the thing being demonstrated.** This repo exists to show multi-agent
orchestration with Microsoft Agent Framework. In-process calls would demonstrate
function composition, which nobody needs a framework for.

**It forces the boundary to be real.** Identity has to propagate as headers, history
has to be rehydrated rather than shared, and failures have to be handled as network
failures. Every one of those is a genuine distributed-systems problem that an
in-process version would let us skip and a production deployment would not.

**It is what makes the .NET stack possible.** Because the contract is HTTP and not a
Python signature, a .NET orchestrator can call a Python specialist and vice versa. The
dual-stack parity gate ([ADR 0005](0005-dual-stack-parity.md)) only exists because this
boundary is a protocol.

## Consequences

Every specialist call costs a network round trip, so the platform makes them
**sequentially, one specialist at a time**, rather than fanning out. `docs/architecture.md`
records the reasoning: each turn is fast enough that sequential stays within acceptable
latency, and later specialists routinely need earlier results — pricing cannot optimise
a cart before product discovery has said what is in it.

The cost is real and was paid twice during this project. The .NET orchestrator could not
reach any specialist for an extended period because a tool parameter name did not match
what the model emitted, and every container reported healthy throughout. An in-process
call would have failed at compile time.

## What would make this wrong

If the orchestrator and specialists were ever deployed as one unit with no independent
scaling or independent failure, the HTTP hop would be pure overhead and this should be
revisited. That is not the case today and is not planned.
