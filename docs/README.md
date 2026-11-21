# Documentation

This folder contains the full technical documentation for E-Commerce Agents.

> You're in `docs/`. For the project README — quick start, screenshots, agent catalog, and demo — see the [project README](../README.md).

---

## Reading order by audience

### "I don't know what an agent is yet"

1. [Concepts](concepts/) — 14 pages, What/Why/When/How, starting from "what is an agent" and
   building up to production concerns. No prior AI/agent knowledge assumed.
2. [Tutorials](../tutorials/) — build a tiny version of each concept yourself, chapter by chapter.
3. Then come back here — the rest of this page assumes the vocabulary those two build.

### "I just want to run it"

1. [Project README → Run it](../README.md#run-it)
2. [Deployment Guide](deployment.md) — Docker Compose profiles, `dev.sh` flags, environment variables
3. [Troubleshooting](troubleshooting.md) — if something doesn't start

### "I want to understand how the agents work"

1. [Architecture](architecture.md) — system overview, agent patterns, A2A protocol, auth flow
2. [Agent Flows](agent-flows.md) — five multi-agent collaboration sequence diagrams
3. [MAF Best Practices](maf-best-practices.md) — `@tool` idioms, middleware, prompt YAML, ContextVars
4. [Adding an Agent](adding-an-agent.md) — step-by-step checklist to scaffold a new specialist

### "I want to choose an orchestration pattern"

1. [Orchestration patterns](concepts/06-orchestration-patterns.md) — the five modes this app runs, and when each fits
2. [Orchestration benchmark](orchestration-benchmark.md) — the same question routed five ways, with latency and cost
3. [Architecture → Orchestration modes](architecture.md#orchestration-modes) — how a request reaches a mode
4. [Approvals](approvals.md) — the human-in-the-loop pause, the routing policy, and resume from a checkpoint
5. [Python vs .NET parity](parity-matrix.md) — what each backend covers

### "I want to build on top of or integrate with this"

1. [API Reference](api-reference.md) — about 70 REST routes; request/response examples for the core surface, tables for the rest
2. [A2A](a2a.md) — the A2A 1.0 endpoint and agent card every specialist serves, and cross-stack interop
3. [MCP Integration](mcp-integration.md) — MCP servers (MCPServer), how to enable, how to inspect
4. [Database Schema](database-schema.md) — 45 tables (34 baseline plus 11 from migrations) with ER diagram and gotchas

### "I care about quality, security, or observability"

1. [Security Guide](security-guide.md) — threat model, guardrails middleware, SQL ownership controls, hardening checklist
2. [Agent Quality & Evals](agent-quality.md) — eval methodology, datasets, red-team suite, CI gate
3. [Agent Audit Matrix](agent-audit-matrix.md) — per-agent security posture and open items
4. [Telemetry](telemetry.md) — OpenTelemetry pipeline, Aspire Dashboard, Langfuse integration

---

## All documents

| Document | What it covers |
|----------|---------------|
| [concepts/](concepts/) | Foundations layer for readers new to AI/agents — 14 pages, What/Why/When/How, each pointing at the live code |
| [architecture.md](architecture.md) | System overview, agent architecture, A2A protocol, auth flow, data flow, technology decisions |
| [a2a.md](a2a.md) | A2A 1.0 on both stacks: the agent card, `/a2a/v1`, task lifecycle, the repo's extensions, `A2A_TRANSPORT`, the signed user-context token (`A2A_IDENTITY`), cross-stack interop, what is not done |
| [adding-an-agent.md](adding-an-agent.md) | Step-by-step checklist to add a new specialist agent |
| [api-reference.md](api-reference.md) | About 70 REST routes (auth, chat, products, orders, cart, admin, marketplace, approvals, proposals, autonomy, returns, notifications) |
| [agent-flows.md](agent-flows.md) | Five multi-agent collaboration sequence diagrams with pattern notes |
| [database-schema.md](database-schema.md) | 45 tables (34 baseline in 12 groups, 11 from migrations), ER diagram, indexing, gotchas |
| [deployment.md](deployment.md) | Docker Compose profiles, `dev.sh` script, environment variables, local dev, health checks |
| [frontend.md](frontend.md) | Next.js 16 routing, theming/OKLCH, SSE streaming, agent timeline, testing |
| [telemetry.md](telemetry.md) | OpenTelemetry instrumentation, Aspire Dashboard, Langfuse LLM observability |
| [mcp-integration.md](mcp-integration.md) | MCP servers (MCPServer), `MCPStreamableHTTPTool` wiring, enable/disable, inspector |
| [maf-best-practices.md](maf-best-practices.md) | MAF `@tool` patterns, middleware, prompt YAML, orchestration patterns |
| [security-guide.md](security-guide.md) | Threat model, guardrails stack, auth/identity propagation, SQL controls, hardening |
| [auth-route-matrix.md](auth-route-matrix.md) | Which routes accept which credential (user JWT, agent secret, service token, none), kept honest by a test |
| [event-autonomy.md](event-autonomy.md) | Event autonomy: autonomy budgets, the append-only decision log, reversal, the event simulator, how an over-budget action becomes an approval |
| [devui.md](devui.md) | Browse the specialists and workflows in MAF DevUI: how to run it without touching the lock, the bound identity, the guard rails |
| [return-lifecycle.md](return-lifecycle.md) | The return lifecycle after approval: waiting for the carrier's scan across restarts, the expiry timer, the admin scan simulator |
| [durable-task-spike.md](durable-task-spike.md) | Write-up: would the Durable Task extension replace the return lifecycle's checkpoints and outbox timers (recommendation: no) |
| [merchant-proposals.md](merchant-proposals.md) | Seller merchant proposals: deterministic markdown/restock suggestions, review through the approvals machinery, idempotent execution, the daily run |
| [approvals.md](approvals.md) | Approval routing policy (`approvals.yaml`), default rules, golden cases, the approvals tables |
| [agent-quality.md](agent-quality.md) | Eval methodology, datasets, red-team scenarios, scoring, CI gate |
| [eval-scorecard.md](eval-scorecard.md) | Generated table of the committed eval baselines per agent, with the replay-fixture caveat |
| [agent-audit-matrix.md](agent-audit-matrix.md) | Per-agent security posture matrix and open hardening items |
| [parity-matrix.md](parity-matrix.md) | Python vs .NET feature parity — honest per-concept status, priority, and tracking issue |
| [troubleshooting.md](troubleshooting.md) | Common local-stack issues and fixes |
| [quick-start.md](quick-start.md) | First run: the no-key path, the demo images, from source, Windows, the .NET stack |
| [configuration.md](configuration.md) | One `.env`, how it reaches each service, every provider and embedding setting |
| [model-providers.md](model-providers.md) | How to run each model provider, what is verified versus untested, known caveats |
| [demo-guide.md](demo-guide.md) | Test users, the agent catalog, scenarios to try, the screens gallery |
| [releasing.md](releasing.md) | Cutting a release: version bump, images on GHCR, retention |
| [roadmap.md](roadmap.md) | What shipped, what is next, what is deliberately not planned |
| [orchestration-benchmark.md](orchestration-benchmark.md) | The same question routed five ways, with latency and cost |
| [reported-vs-actual.md](reported-vs-actual.md) | Bugs that turned out bigger than the report said, and how each was found |
| [azure-deployment.md](azure-deployment.md) | Draft Container Apps deployment: topology, scripts, costs, assumptions still to confirm |
| [workflows/](workflows/README.md) | The workflow graphs behind the two workflow modes |
| [adr/](adr/README.md) | Architecture decision records |

---

## Diagrams

Mermaid diagrams are embedded across these docs (they render as diagrams on the published site). The static system architecture image is [`architecture.png`](architecture.png) (editable source: [`architecture.drawio`](architecture.drawio)). Screenshots of the running application are in [`images/`](images/).
