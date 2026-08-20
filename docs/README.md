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

1. [Project README → Quick Start](../README.md#quick-start)
2. [Deployment Guide](deployment.md) — Docker Compose profiles, `dev.sh` flags, environment variables
3. [Troubleshooting](troubleshooting.md) — if something doesn't start

### "I want to understand how the agents work"

1. [Architecture](architecture.md) — system overview, agent patterns, A2A protocol, auth flow
2. [Agent Flows](agent-flows.md) — five multi-agent collaboration sequence diagrams
3. [MAF Best Practices](maf-best-practices.md) — `@tool` idioms, middleware, prompt YAML, ContextVars
4. [Adding an Agent](adding-an-agent.md) — step-by-step checklist to scaffold a new specialist

### "I want to build on top of or integrate with this"

1. [API Reference](api-reference.md) — all 20 REST endpoints with request/response examples
2. [MCP Integration](mcp-integration.md) — MCP servers (FastMCP), how to enable, how to inspect
3. [Database Schema](database-schema.md) — 24 tables with ER diagram and gotchas

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
| [adding-an-agent.md](adding-an-agent.md) | Step-by-step checklist to add a new specialist agent |
| [api-reference.md](api-reference.md) | All 20 REST endpoints (auth, chat, products, orders, admin, marketplace) |
| [agent-flows.md](agent-flows.md) | Five multi-agent collaboration sequence diagrams with pattern notes |
| [database-schema.md](database-schema.md) | 24 tables in 8 logical groups, ER diagram, indexing, gotchas |
| [deployment.md](deployment.md) | Docker Compose profiles, `dev.sh` script, environment variables, local dev, health checks |
| [frontend.md](frontend.md) | Next.js 16 routing, theming/OKLCH, SSE streaming, agent timeline, testing |
| [telemetry.md](telemetry.md) | OpenTelemetry instrumentation, Aspire Dashboard, Langfuse LLM observability |
| [mcp-integration.md](mcp-integration.md) | MCP servers (FastMCP), `MCPStreamableHTTPTool` wiring, enable/disable, inspector |
| [maf-best-practices.md](maf-best-practices.md) | MAF `@tool` patterns, middleware, prompt YAML, orchestration patterns |
| [security-guide.md](security-guide.md) | Threat model, guardrails stack, auth/identity propagation, SQL controls, hardening |
| [agent-quality.md](agent-quality.md) | Eval methodology, datasets, red-team scenarios, scoring, CI gate |
| [agent-audit-matrix.md](agent-audit-matrix.md) | Per-agent security posture matrix and open hardening items |
| [troubleshooting.md](troubleshooting.md) | Common local-stack issues and fixes |

---

## Diagrams

23 Mermaid diagrams are embedded across these docs. The static system architecture image is [`architecture.png`](architecture.png) (editable source: [`architecture.drawio`](architecture.drawio)). Screenshots of the running application are in [`images/`](images/).
