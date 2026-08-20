# Python vs .NET Feature Parity Matrix

This repo ships two complete backends over the same domain: `agents/python/` (Microsoft
Agent Framework's Python SDK) and `agents/dotnet/` (the .NET/C# SDK). Development here is
**Python-first** — new capabilities land in Python first, and the .NET backend follows on a
prioritized backlog rather than in lockstep. This matrix replaces an earlier, less accurate
"feature-parity" claim in the root `README.md` with an honest, per-concept breakdown of what's
actually implemented on each side today, verified directly against the code (not carried
forward from an older description).

See also [`docs/agent-audit-matrix.md`](agent-audit-matrix.md) for the security-specific
breakdown (injection defense, role enforcement, eval/red-team coverage) — this document covers
the wider feature surface; the two overlap on guardrails and cross-link rather than duplicate.

**Status legend**

| Status | Meaning |
|--------|---------|
| Full | Implemented and wired into the live request path on both sides |
| Partial | Present but incomplete, or present in one form and not the equivalent form (noted inline) |
| Python-first — planned | Python has it; .NET doesn't yet, and it's on the backlog below |
| Not supported by MAF .NET | Blocked on the underlying .NET SDK, not a gap this repo can close alone |

**Priority** reflects the backlog order in [issue #11](https://github.com/nitin27may/e-commerce-agents/issues/11)'s
linked gaps — P1 first.

---

## Matrix

| # | Concept | Python | .NET | Status | Priority | Issue |
|---|---------|--------|------|--------|----------|-------|
| 1 | Middleware / context-provider pipeline attached to agents | `shared/middleware.py`'s `build_specialist_middleware()` wired into every specialist's `Agent(...)` construction | `Shared/Agents/SpecialistPipeline.cs` composes `AgentRunLogger`, `ToolAuditMiddleware`, and `PiiRedactor` via `AIAgentBuilder`'s `.Use(...)` pipeline (agent-run + function-invocation seams, MAF .NET 1.18+); `ContextEnricher` is attached via a new `EcommerceContextProvider : AIContextProvider` on `ChatClientAgentOptions.AIContextProviders`. `SpecialistAgentFactory.Create()` applies both whenever a caller passes its `IServiceProvider`, which all 6 agent-construction call sites (5 specialists + orchestrator) now do | Full | — | [#12](https://github.com/nitin27may/e-commerce-agents/issues/12) (closed) |
| 2 | MCP server protocol | Two real FastMCP servers (`packages/mcp-product`, `packages/mcp-inventory`) — streamable-HTTP transport, real JSON-RPC | `ECommerceAgents.Mcp` now uses the official `ModelContextProtocol.AspNetCore` SDK — real JSON-RPC over streamable HTTP (`MapMcp("/mcp")`), `[McpServerToolType]`/`[McpServerTool]` tools, verified live (`initialize` + `tools/list` + `tools/call` over the wire) and via an end-to-end `McpClient` test | Full | — | [#13](https://github.com/nitin27may/e-commerce-agents/issues/13) (closed) |
| 3 | Streaming chat to specialists (`/message:stream`) | `shared/agent_host.py` exposes both `/message:send` and `/message:stream` (SSE) on every specialist; `orchestrator/agent.py`'s `call_specialist_agent` consumes the specialist's stream and forwards live `event: delta` frames while the tool call is in flight | `AgentHost.cs` now maps `POST /message:stream` on every specialist (`RunAgentWithHistoryStreamingAsync`); `A2AClient.StreamAsync` consumes it; `OrchestratorTools.CallSpecialistAgent` forwards each delta into a request-scoped `Channel<string>` (`RequestContext.StreamScope`, the .NET analog of Python's `current_stream_queue`) that `ChatRoutes.StreamAsync` drains concurrently into `event: delta` frames on the outer SSE response — same live-preview behavior as Python's `tool` mode. Scoped to the `tool` orchestration mode only; .NET has no other orchestration modes to extend this to (see rows 11-13) | Full | — | [#14](https://github.com/nitin27may/e-commerce-agents/issues/14) (closed) |
| 4 | Inbound prompt-injection detection | `shared/guardrails/injection_middleware.py`, attached via the shared middleware pipeline | Not present | Python-first — planned | P2 | [#15](https://github.com/nitin27may/e-commerce-agents/issues/15) |
| 5 | Stored-content sanitization (tool results re-entering the model) | `shared/guardrails/output_middleware.py` | Not present — see also [`docs/agent-audit-matrix.md`](agent-audit-matrix.md#dimensions), which already calls this out as Python-only | Python-first — planned | P2 | [#15](https://github.com/nitin27may/e-commerce-agents/issues/15) |
| 6 | Output moderation (self-harm / hate / violence phrase screening) | `shared/guardrails/moderation.py` + `moderation_middleware.py` | Not present | Python-first — planned | P2 | [#15](https://github.com/nitin27may/e-commerce-agents/issues/15) |
| 7 | Step recorder → live agentic timeline | `shared/agent_observability.py`'s `StepRecorderMiddleware`, attached to every agent, drained per-request into SSE `event: step` frames and the `/runs` UI | `UsageRecorder.LogExecutionStepAsync()` exists and can write to the same `agent_execution_steps` table, but is never called from anywhere in `agents/dotnet/src` — a real method with zero call sites, not a working feature | Partial | P2 | [#16](https://github.com/nitin27may/e-commerce-agents/issues/16) |
| 8 | Fan-out/fan-in workflow construction | `workflows/pre_purchase.py` uses MAF's `WorkflowBuilder` — a real executor graph, checkpointable, observable via `event: node` frames | `PrePurchaseWorkflow.cs` uses a plain `Task.WhenAll(reviewsTask, stockTask, priceTask)` — concurrent calls, but no graph, no checkpoint, no node-level events | Partial | P2 | [#17](https://github.com/nitin27may/e-commerce-agents/issues/17) |
| 9 | Human-in-the-loop as middleware | `shared/hitl.py` intercepts five destructive tools at the middleware layer, independent of each tool's own body | `HitlApprovalMiddleware` is DI-injected directly into tool classes and called from inside each tool method (`_hitl.GuardAsync(...)` in `OrderTools.cs`, `InventoryTools.cs`) — a per-call-site wrapper, not an interception layer | Partial | P2 | [#17](https://github.com/nitin27may/e-commerce-agents/issues/17) |
| 10 | Shared tool library | `shared/tools/` — 8 modules, 1,473 lines, imported by whichever specialists need them (`cart_tools.py`, `return_tools.py`, `seller_tools.py`, `loyalty_tools.py`, `inventory_tools.py`, `user_tools.py`, `memory_tools.py`, `pricing_tools.py`) | No `Shared/Tools/` directory — each specialist has its own `Tools/` folder with fully duplicated logic (`ReviewTools.cs` 875 lines, `PricingTools.cs` 705, `InventoryTools.cs` 661, `OrderTools.cs` 587, `ProductTools.cs` 398 — 3,226 lines total, none shared) | Partial | P3 | [#18](https://github.com/nitin27may/e-commerce-agents/issues/18) |
| 11 | Handoff orchestration | `orchestrator/handoff.py`'s `HandoffBuilder` mesh — a real, live-reachable orchestration mode | Config surface only (`AgentSettings.HandoffAutonomousMode`, `MafHandoffMode`) — no `HandoffBuilder`-equivalent engine implements it | Python-first — planned | P3 | [#19](https://github.com/nitin27may/e-commerce-agents/issues/19) |
| 12 | Group chat orchestration | `workflows/group_chat.py` — two agent panelists + a moderator | Not present | Python-first — planned | P3 | [#19](https://github.com/nitin27may/e-commerce-agents/issues/19) |
| 13 | Magentic orchestration | `orchestrator/modes/magentic_mode.py` | Not present | Python-first — planned | P3 | [#19](https://github.com/nitin27may/e-commerce-agents/issues/19) |
| 14 | Eval harness | `evals/harness.py`'s `ProductionRunner`, real scorers, committed baselines, CI-gated smoke suite | Not present | Python-first — planned | P3 | [#19](https://github.com/nitin27may/e-commerce-agents/issues/19) |
| 15 | Long-term memory — write path | `shared/tools/memory_tools.py` — agent-callable save/update tools | Read-only: `ContextEnricher.GetMemoriesAsync()` injects existing memories into context, but no tool lets a .NET agent write a new one | Partial | P3 | [#19](https://github.com/nitin27may/e-commerce-agents/issues/19) |
| 16 | Tutorial chapter test coverage (`tutorials/*/dotnet/`) | All 32 chapters have Python tests (non-integration suite green in CI) | Real code + tests: ch01–11. Real code, no tests: ch12–20 (sequential, concurrent, handoff, group chat, magentic, HITL, checkpoints, declarative, visualization). Stub only: ch20b (README only, no `.csproj`), ch21 (`.gitkeep` only). Absent entirely: ch22–32 (eleven chapters — Python-only, includes evals, cost-control, RAG, guardrails, and more) | Partial | P3 | [#20](https://github.com/nitin27may/e-commerce-agents/issues/20) |

---

## What's not on this list

Both backends implement the domain fully — six specialist agents, A2A routing, the tool router
mode, checkpointing (Postgres-backed on both sides), OAuth2/JWT auth, and the Aspire-based
telemetry pipeline. This matrix tracks the *gaps*; the shared foundation isn't repeated here row
by row.

## Out of scope for now

Per the repo's Python-first direction, none of the P3 rows above are scheduled — .NET readers
should treat handoff, group chat, magentic, evals, and long-term-memory writes as capabilities
this backend doesn't have yet, not capabilities coming soon. P1/P2 rows are the nearer-term
backlog, tracked individually at the linked issues so each can be picked up independently of this
document.
