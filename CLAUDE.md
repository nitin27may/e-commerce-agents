# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

E-Commerce Agents is a multi-agent e-commerce platform built with **Microsoft Agent Framework (MAF)** Python SDK. 6 specialized agents collaborate via **A2A protocol** to handle product discovery, orders, pricing, reviews, inventory, and customer support. Includes a marketplace layer with agent catalog, access requests, and admin approval.

Companion demo repo for the AI article series on nitinksingh.com.

## Working Artifacts Location

All working artifacts — **memory, rules, and plans** — live in the **repo-local `.claude/` folder
only**:

- `.claude/memory/` — project memory and notes
- `.claude/rules/` — repo-specific rules
- `.claude/plans/` — implementation plans (master + sub-plans, e.g. `.claude/plans/enhancements/`)

Do not put these in the repo root (no top-level `plans/` folder) or rely on global `~/.claude`.
Keeping them under the repo-local `.claude/` means they are committed with the project and travel
with it.

## Key Commands

```bash
# Start everything from scratch (one command)
./scripts/dev.sh

# Clean rebuild (nuke volumes, rebuild images)
./scripts/dev.sh --clean

# Start infrastructure only (db, redis, aspire)
./scripts/dev.sh --infra-only

# Re-run seeder against existing DB
./scripts/dev.sh --seed-only

# Start everything via docker compose directly
docker compose up --build

# Run a single agent locally (for dev)
cd agents/python && uv run uvicorn product_discovery.main:app --port 8081 --reload

# Run frontend locally
cd web && pnpm dev

# Generate embeddings
cd agents/python && uv run python -m scripts.generate_embeddings

# Lint Python
cd agents/python && uv run ruff check .
cd agents/python && uv run ruff format --check .

# Run Python tests
cd agents/python && uv run pytest
cd agents/python && uv run pytest tests/test_specific.py -k "test_name"

# Lint frontend
cd web && pnpm lint

# Run Playwright E2E tests (requires running app at localhost:3000)
cd web && pnpm exec playwright test
cd web && pnpm exec playwright test e2e/chat-all-users.spec.ts

# Open Aspire Dashboard (telemetry visualization)
open http://localhost:18888
```

## Architecture Overview

**Request flow**: Browser -> Next.js frontend (:3000) -> Orchestrator FastAPI (:8080) -> Specialist agents via A2A (:8081-8085) -> PostgreSQL/Redis

The **orchestrator** is the front door. All user requests go through it. Its LLM calls `call_specialist_agent()` tool to route to the appropriate specialist via HTTP POST to `/message:send`.

Each specialist agent runs as an independent microservice with its own port and A2A endpoint, but all share a single Dockerfile (multi-target via `ARG AGENT_NAME`).

### Orchestrator route layout

`orchestrator/routes/` is a package, not a single file: `chat.py` holds `/api/chat` + `/api/chat/stream`, `orchestration.py` holds the mode-introspection endpoints (`/api/orchestration/modes`, `.../graph`, `.../compare` — real as of Phase 1.6c, runs one prompt through several modes sequentially and reports per-mode text/latency/steps/graph; `tokens`/`est_cost_usd`/`grounding` aren't in the response yet, since they need Phase 3.5/Phase 2 infrastructure that doesn't exist — and `.../{run_id}/resume`, real as of Phase 1.5), and `legacy.py` holds everything else (auth, marketplace, admin, cart, checkout, orders, seller, and — Phase 1.5 — `GET /api/runs/{id}/checkpoints`) — the pre-split monolith, kept together since the split's purpose was isolating chat, not fully decomposing the route surface. `orchestrator/routes/__init__.py` combines all three into the single `router` `orchestrator/main.py` includes, and re-exports `optional_auth`/`require_auth`/`require_admin`/`require_seller`/`settings` so existing import sites are unaffected.

**Web UI (Phase 1.6).** `web/src/components/chat/mode-switcher.tsx` (1.6a) is a composer control fed by `GET /api/orchestration/modes`, threading the selected mode through `api.ts::chatStream()`'s `mode` option and persisting it per conversation in localStorage. `orchestration-graph.tsx` (1.6b) renders a mode's `graph_mermaid()` output client-side (house palette applied client-side — the backend's generated source carries no styling) and animates nodes live from the `onOrchestrationEvent` hook's `node`/`error` frames; correlating a live `node_id` to a diagram node relies on a deliberate backend convention — every workflow mode's `graph_mermaid()` uses the real executor id with dashes swapped for underscores as the Mermaid node id (see `PrePurchaseMode.graph_mermaid()`'s comment in `workflow_mode.py`). `mode-comparison.tsx` (1.6c) is a standalone dialog (not tied to the active conversation) that calls `POST /api/orchestration/compare` and renders a result card per mode, reusing `orchestration-graph.tsx` in a static (non-live) rendering by passing the mode's `agents_involved` as `doneNodeIds`.

Both chat endpoints resolve an orchestration mode (request body `mode` -> `settings.ORCHESTRATION_MODE` -> `"tool"`) via `orchestrator.modes.get_mode()` and dispatch through it — `orchestrator/modes/` is what makes those modes reachable from a live request rather than tests-only. Five are registered: `tool` (default, LLM calls `call_specialist_agent`), `handoff` (`orchestrator/handoff.py`'s `HandoffBuilder` mesh), `workflow:pre-purchase` (`workflows/pre_purchase.py`'s concurrent fan-out/fan-in), `workflow:return-replace` (`workflows/return_replace.py`'s sequential graph with an in-workflow `ctx.request_info` HITL gate), and `group-chat` (`workflows/group_chat.py`'s round-table debate, two agent-backed panelists then a moderator). The two workflow modes and group-chat resolve a product_id/order_id out of the chat message themselves (a UUID literal if present, else a lookup — `search_products` / the user's most recent order) since neither underlying workflow ever took free text.

**Checkpoints + resume (Phase 1.5).** `PrePurchaseMode` and `ReturnReplaceMode` attach `shared.factory.get_checkpoint_storage()` to every workflow run, wrapped in `shared.checkpoint_storage.RecordingCheckpointStorage` so each save surfaces as its own `kind="checkpoint"` event (MAF's own event stream never mentions a save). `chat.py`'s `_link_run_artifacts()` correlates a run's `usage_logs` row to its checkpoints (`workflow_checkpoints.usage_log_id`) and, when `workflow:return-replace` pauses, writes a `hitl_requests` row carrying MAF's own resume token (`request_id`) and the paused checkpoint. `ReturnReplaceMode.resume()` — reached from `POST /api/orchestration/{run_id}/resume` — builds a *fresh* `Workflow` object (there's no live one to reuse; the one that paused lived in a prior request's process memory) and resumes purely from `checkpoint_id` + `responses={request_id: approved}`, verified directly to replay correctly through discount and finalize. Note: `_HitlGateExecutor.on_approval()` (`workflows/return_replace.py`, unchanged) rebuilds a minimal `WorkflowState` from the approval-request snapshot on resume — `return_id`/`replacement_products` from the original run don't survive; `resume()`'s response text is written to not depend on them.

`chat_stream()` branches on the resolved mode: `tool` keeps its original direct-agent SSE path (no mode streams token-level deltas the way `_run_agent_native_stream` does), while every other mode streams through `get_mode(...).run()` directly, translating each `OrchestrationEvent` into an SSE frame — `delta` becomes real display text (extracted via `orchestrator.events.delta_text`) when it's present, `node_enter`/`node_exit` becomes `event: node`, `handoff` becomes `event: handoff`, `tool_call` becomes `event: step`, `checkpoint`/`request_info`/`error` get their own named frames. Not every mode streams incremental text — `handoff` does (its "output" events carry `AgentResponseUpdate` content), but the three MAF-workflow modes mostly don't (their executors call `ctx.send_message()`, which produces no delta; the one `ctx.yield_output()` carries a raw state dataclass) — so `_run_mode_task()` falls back to pushing `run_completed`'s full text as a single chunk if nothing streamed incrementally, guaranteeing every mode a non-empty visible response. The web client (`web/src/lib/api.ts::chatStream`) routes any SSE event name outside `step`/`metadata`/`delta`/plain-text to an `onOrchestrationEvent` hook rather than rendering it as chat text — no UI consumes these frames yet (that's Phase 1.6), but the parser had to be fixed to not corrupt the chat bubble with raw JSON once a caller sets `mode` to anything other than `tool`.

### MAF-Native Execution in agent_host.py

`shared/agent_host.py` is a lightweight A2A-compatible FastAPI host. It does **not** implement a custom tool-calling loop — the legacy `_run_agent_with_tools()` / `_run_agent_with_tools_stream()` OpenAI chat-completions loop was removed once MAF-native execution was confirmed compatible with production Azure deployments (see the module docstring). Every request now goes through MAF's own `agent.run()` (blocking) / `agent.run(..., stream=True)` (SSE streaming); the `Agent` object owns its tools, system prompt, and context-provider chain, so `agent_host.py` just threads the A2A request into the right MAF call and (for streaming) forwards chunks over SSE.

### MAF Package Patch

`agents/python/patch_maf.py` — workaround for a packaging bug in `agent-framework-core==1.0.0`, whose `__init__.py` shipped empty with no public re-exports. Fixed upstream by 1.14.0 (the version this repo now pins), so `patch()` is already a no-op on a current install (it only writes when the file is empty). The Dockerfile still runs it defensively; it does nothing today.

### YAML Prompt Composition System

Prompts are NOT hardcoded strings. `shared/prompt_loader.py` loads from `agents/python/config/prompts/{agent_name}.yaml` and composes: base prompt + grounding-rules (shared) + role-specific instructions + schema context + tool examples.

Shared prompt fragments live in `agents/python/config/prompts/_shared/` (grounding-rules.yaml, schema-context.yaml, tool-examples.yaml).

Each agent's `create_*_agent()` factory calls `get_system_prompt(current_user_role.get())` (which wraps `load_prompt(agent_name, user_role)`) at agent-construction time, and agents are rebuilt on every request (see `orchestrator/routes/chat.py`) — so the composed prompt is genuinely role-aware per request (admin sees different instructions than customer). Earlier revisions built the prompt once at *import* time with a hardcoded default role, which silently defeated the role-specific YAML blocks; that bug is fixed.

### Auth & Identity Flow

- **External requests**: JWT Bearer token validated by `AgentAuthMiddleware` in `shared/auth.py`
- **Inter-agent requests**: `X-Agent-Secret` header (shared secret) + `X-User-Email` / `X-User-Role` headers
- **Identity propagation**: Auth middleware sets ContextVars (`current_user_email`, `current_user_role`, `current_session_id`) which tools read directly — no parameter passing through the call stack

### Conversation History Forwarding

The orchestrator no longer forwards a truncated copy of the conversation history on A2A calls (that "last 10 messages, 500 chars each" window was removed — see the comment in `orchestrator/agent.py::call_specialist_agent`). Instead only the session id travels, via the `x-session-id` header; each specialist rehydrates prior context itself by querying Postgres for the session's messages (`shared/agent_host.py::_rehydrate_history_from_session`) when it needs to handle a follow-up contextually.

Separately, the orchestrator's own read of *its* conversation's history (for `RunContext.history`, forwarded into every orchestration mode) goes through `shared/session.py::get_history_as_dicts` + `get_history_provider`, not a hand-rolled `SELECT`. Message *writes* stay as each route's own richer `INSERT` (carries `agent_name`/`agents_involved`/`metadata` a generic `HistoryProvider` write doesn't) — see `orchestrator/routes/chat.py`'s module docstring for why a `HistoryProvider` isn't attached as an automatic `context_providers=[...]` hook (verified it would double-write).

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Agent Framework | `agent-framework` v1.0 (MAF Python SDK, beta) |
| Agent Communication | A2A Protocol (HTTP POST to `/message:send`) |
| LLM | OpenAI / Azure OpenAI (gpt-4.1), configurable via `LLM_PROVIDER` env var |
| Backend | Python 3.12, FastAPI (orchestrator), Starlette (specialist agents via agent_host) |
| Database | PostgreSQL 16 + pgvector (1536-dim embeddings for text-embedding-3-small) |
| Cache | Redis 7 |
| Frontend | Next.js 16, React 19, Tailwind CSS 4, shadcn/ui |
| Auth | Self-contained JWT (PyJWT + bcrypt), no external IdP |
| Telemetry | OpenTelemetry -> .NET Aspire Dashboard (:18888) |
| Package Managers | `uv` (Python), `pnpm` (Node) |
| E2E Tests | Playwright (chromium, sequential, `web/e2e/`) |
| Linting | ruff (Python, line-length 120, py312), ESLint 9 (TypeScript) |

## Specialist Agent Pattern

Each specialist agent follows this structure:
```
agent.py    -> create_*_agent() returning Agent with tools list + context providers
tools.py    -> @tool functions for this agent's domain (DB access via get_pool())
prompts.py  -> loads SYSTEM_PROMPT from YAML via prompt_loader
main.py     -> create_agent_app() entry point with telemetry + DB pool init in lifespan
```

Tools use MAF's `@tool` decorator with `Annotated` type hints (not Pydantic input models). All tools are `async` and access the database directly via `get_pool()` — no context dict passing.

## Frontend Notes

- **Next.js 16.x** — this version has breaking changes from training data. Always read `node_modules/next/dist/docs/` before writing frontend code.
- App Router with `(app)/` group for authenticated layout (sidebar + navigation)
- Auth via `lib/auth-context.tsx` (React context, localStorage persistence, JWT tokens)
- API client singleton in `lib/api.ts` — all backend calls go through this
- Chat interface supports SSE streaming via `chatStream()`
- Rich message rendering: markdown + product cards + order cards in `components/chat/`

## LLM Provider Configuration

Controlled via `LLM_PROVIDER` environment variable. Both providers use the same `agent-framework` ChatClient interface.

```bash
# OpenAI (default for local dev)
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
LLM_MODEL=gpt-4.1

# Azure OpenAI
LLM_PROVIDER=azure
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_KEY=...
AZURE_OPENAI_DEPLOYMENT=gpt-4.1
AZURE_OPENAI_API_VERSION=2024-12-01-preview
```

## Database

- Schema in `docker/postgres/init.sql` (24 tables)
- All queries use parameterized `$1, $2` syntax (asyncpg)
- All user-facing queries filter by `user_email` or `user_id`
- Embeddings stored as `vector(1536)` with ivfflat cosine index
- Seeder (`scripts/seed.py`) is deterministic (`random.seed(42)`) — reproducible demo data

## Coding Conventions

- MAF `@tool` decorator with `Annotated` type hints
- `async` everywhere — all tools, all DB queries, all HTTP calls
- `asyncpg` for PostgreSQL (connection pool via `get_pool()`, not ORM)
- `httpx` for async HTTP (never `requests`)
- Pydantic Settings for configuration (`shared/config.py`)
- ContextVars for request-scoped state (`shared/context.py`)
- Type hints on all functions
- f-strings for string formatting
- Guard clauses for early returns

## Do Not

- Use Ollama or local models — this demo targets OpenAI / Azure OpenAI only
- Create custom tool registries — use MAF's built-in `@tool`
- Write raw OpenAI function-calling loops — use the existing `agent_host.py` pattern
- Use `pip` or `poetry` — use `uv` for Python
- Use `npm` or `yarn` — use `pnpm` for Node
- Skip type hints on any function
- Use `requests` — use `httpx` for async HTTP
- Hardcode prompts in Python — use YAML config in `agents/python/config/prompts/`
- Pass user identity as function args — use ContextVars from `shared/context.py`
