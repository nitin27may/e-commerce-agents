# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

E-Commerce Agents is a multi-agent e-commerce platform built with **Microsoft Agent Framework (MAF)** Python SDK. 6 specialized agents collaborate via **A2A protocol** to handle product discovery, orders, pricing, reviews, inventory, and customer support. Includes a marketplace layer with agent catalog, access requests, and admin approval.

Companion demo repo for the AI article series on nitinksingh.com.

## Working Artifacts Location

All working artifacts live in the **repo-local `.claude/` folder only**:

- `.claude/plans/` — **one** consolidated plan (`remaining-work.md`). Finished plans are deleted
  rather than archived: git keeps them, and a directory of stale plans makes "what is left?" a
  research question every time it is asked
- `.claude/agents/` — the subagent definitions and the model-strategy README
- `.claude/settings.json` — the session model (`opusplan`)

There is deliberately no `.claude/memory/` or `.claude/rules/`: durable facts belong in this file
or in `AGENTS.md`, and anything short-lived belongs in the one plan. Earlier revisions of this
section described both directories; neither has ever existed.

Do not put these in the repo root (no top-level `plans/` folder) or rely on global `~/.claude`.
Keeping them under the repo-local `.claude/` means they are committed with the project and travel
with it.

Resuming after a break, or in a new session (local or cloud)? Read `.claude/HANDOVER.md` first: current state,
what is blocked and on whom, the procedures that took time to learn, and a progress log to keep updated.

## Key Commands

```bash
# Start everything from scratch (one command)
./scripts/dev.sh
./scripts/dev.sh --demo       # pull the prebuilt GHCR images instead of building (not with --dotnet)
./scripts/dev.sh --dotnet --switch   # tear the other stack down first, volumes and all
./scripts/dev.ps1              # PowerShell equivalent — same flags, PascalCase
                               # (-Clean / -SeedOnly / -InfraOnly / -Dotnet / -Demo / -SwitchStack)

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

# Embeddings are seeded automatically (the seeder fills product_embeddings).
# Regenerate, or export the precomputed file a keyed install loads:
uv run --project agents/python python -m scripts.generate_embeddings
uv run --project agents/python python -m scripts.generate_embeddings --export scripts/data/product_embeddings.v1.jsonl.gz

# Apply schema migrations (the seeder and dev.sh do this for you)
uv run --project agents/python python -m scripts.migrate

# Replay eval suite, keyless — the same script CI runs
scripts/replay-evals.sh --setup          # migrate + seed a fresh DB, then every suite
cd agents/python && uv run python -m evals.fixture_drift   # 0 clean · 1 offline-fixable · 2 re-record

# Live evals (dispatch-only in CI; runs locally too, spends a key). Cost first, no model called:
cd agents/python && uv run python -m evals.run_evals --estimate gpt-4.1-mini --agent product-discovery --dataset evals/datasets/product_discovery.json
scripts/live-evals.sh --check-env        # the same script the manual `full` job runs; see agents/python/evals/README.md

# Effective configuration with secrets redacted, and run profiles (a profile holds no secret)
uv run --project agents/python python -m scripts.doctor
./scripts/dev.sh --profile ollama        # dev.ps1 -Profile; also --no-key for the replay-backed demo; table in docs/run-profiles.md

# Docs checks (CI: jekyll-gh-pages.yml, tutorials.yml)
python3 scripts/build_docs_site.py --check     # nav, links, diagrams, parity-matrix summary
python3 scripts/check_tutorial_readmes.py --check && python3 scripts/check_tutorial_coverage.py --check && python3 scripts/check_tutorial_nav.py --check
python3 scripts/build_eval_scorecard.py --check && python3 scripts/check_maf_badge.py

# Land a green PR only when its head SHA did not move (see docs/contributing-dependencies.md)
scripts/merge-when-green.sh <pr>

# Lint Python
cd agents/python && uv run ruff check .
cd agents/python && uv run ruff format --check .

# A2A 1.0 conformance (official a2a-sdk client against a real specialist app), and the replay gate over that wire
cd agents/python && uv run pytest tests/test_a2a_conformance.py tests/test_a2a_transport.py -v
A2A_TRANSPORT=a2a scripts/replay-evals.sh   # (from the repo root)

# .NET side of the same contract, and the signed user-context token on both stacks
cd agents/dotnet && dotnet test tests/ECommerceAgents.Shared.Tests --filter "FullyQualifiedName~A2A|FullyQualifiedName~UserContextToken"
cd agents/python && uv run pytest tests/test_identity_token.py -v

# Cross-process interop, both directions, keyless (real specialists on real sockets around scripted models;
# the official Python client against a .NET specialist and the official .NET client against a Python one)
scripts/a2a-interop.sh

# Mixed stacks: Python orchestrator + .NET specialists (py-net) and the reverse (net-py). Needs Docker and a model key to run
scripts/e2e-both-stacks.sh --mixed -- e2e/orchestration-parity.spec.ts

# Run Python tests (--all-packages pulls in the MCP workspace packages, e.g. ecommerce-mcp-product)
cd agents/python && uv sync --all-packages --extra dev
cd agents/python && uv run pytest
cd agents/python && uv run pytest tests/test_specific.py -k "test_name"

# Lint frontend
cd web && pnpm lint

# Run Playwright E2E tests (requires running app at localhost:3000)
cd web && pnpm exec playwright test
cd web && pnpm exec playwright test e2e/chat-all-users.spec.ts

# Browse the specialists and workflows in MAF DevUI (dev only; added to the run, not the lock: docs/devui.md)
uv run --project agents/python --with agent-framework-devui==1.0.0b261112 python -m scripts.devui

# Open Aspire Dashboard (telemetry visualization)
open http://localhost:18888
```

## Architecture Overview

**Request flow**: Browser -> Next.js frontend (:3000) -> Orchestrator FastAPI (:8080) -> Specialist agents via A2A (:8081-8085) -> PostgreSQL/Redis

The **orchestrator** is the front door. All user requests go through it. Its LLM calls `call_specialist_agent()` tool to route to the appropriate specialist via HTTP POST to `/message:send`.

Each specialist agent runs as an independent microservice with its own port and A2A endpoint, but all share a single Dockerfile (multi-target via `ARG AGENT_NAME`).

### Orchestrator route layout

`orchestrator/routes/` is a package, not a single file: `chat.py` holds `/api/chat` + `/api/chat/stream`, `orchestration.py` holds the mode-introspection endpoints (`/api/orchestration/modes`, `.../graph`, `.../compare` — real as of Phase 1.6c, runs one prompt through several modes sequentially and reports per-mode text/latency/steps/graph; each result carries `tokens_in`/`tokens_out`/`est_cost_usd`, null meaning "not captured" and an explicit zero for the two workflow modes, which call no model; `grounding` is not in the response — and `.../{run_id}/resume`, real as of Phase 1.5), and `legacy.py` holds everything else (auth, marketplace, admin, cart, checkout, orders, seller, and — Phase 1.5 — `GET /api/runs/{id}/checkpoints`) — the pre-split monolith, kept together since the split's purpose was isolating chat, not fully decomposing the route surface. Six smaller modules sit beside them: `approvals.py` (the staff approvals inbox and decide endpoint — see *Approvals* below), `notifications.py` (`/api/me/notifications`, the parked-outbox admin list), `demo.py` (`/api/demo/tour`, `/api/demo/reset`, only when `DEMO_TOUR_ENABLED`), `autonomy.py` (`/api/admin/events/simulate`, `/api/admin/decisions*`, see *Event autonomy*), `proposals.py` (`/api/seller/proposals*`, see *Merchant proposals*) and `returns_lifecycle.py` (`simulate-scan`, `/api/staff/returns/*`, `/api/runs/{id}/timeline`, see *Return lifecycle*). `orchestrator/routes/__init__.py` combines them all into the single `router` `orchestrator/main.py` includes, and re-exports `optional_auth`/`require_auth`/`require_admin`/`require_staff`/`require_seller`/`settings` so existing import sites are unaffected.

**Web UI (Phase 1.6).** `web/src/components/chat/mode-switcher.tsx` (1.6a) is a composer control fed by `GET /api/orchestration/modes`, threading the selected mode through `api.ts::chatStream()`'s `mode` option and persisting it per conversation in localStorage. `orchestration-graph.tsx` (1.6b) renders a mode's `graph_mermaid()` output client-side (house palette applied client-side — the backend's generated source carries no styling) and animates nodes live from the `onOrchestrationEvent` hook's `node`/`error` frames; correlating a live `node_id` to a diagram node relies on a deliberate backend convention — every workflow mode's `graph_mermaid()` uses the real executor id with dashes swapped for underscores as the Mermaid node id (see `PrePurchaseMode.graph_mermaid()`'s comment in `workflow_mode.py`). `mode-comparison.tsx` (1.6c) is a standalone dialog (not tied to the active conversation) that calls `POST /api/orchestration/compare` and renders a result card per mode, reusing `orchestration-graph.tsx` in a static (non-live) rendering by passing the mode's `agents_involved` as `doneNodeIds`.

**Resume UI (`/runs`).** `web/src/app/(app)/runs/page.tsx` fetches `GET /api/runs/{id}/checkpoints` for every visible run up front (not lazily on row-expand — a lazy fetch means a "Needs approval" badge can never show until a row's already been opened once, defeating its purpose as something to scan the list for), and shows Approve/Reject buttons calling `POST /api/orchestration/{run_id}/resume` for any row with a pending `hitl_request` — to staff only. A reviewer can never be the requester, on either stack. On both stacks, resume is a thin caller of the approvals decide function: staff means `support` or `admin`, and the reviewer's role must be at or above the level the policy assigned (`support` cannot decide a `manager` route). Everyone else sees "Awaiting staff review", in `/runs` and on the in-chat approval card alike. The resumed workflow executes as the original requester (tools read identity from ContextVars), with the reviewer recorded separately. Rows are always expandable now, not gated on `steps.length > 0` — workflow modes never populate `agent_execution_steps` (only "tool" mode's `log_execution_step()` calls do), so that gate used to hide the expand affordance for exactly the runs most likely to have a pending approval.

Both chat endpoints resolve an orchestration mode (request body `mode` -> `settings.ORCHESTRATION_MODE` -> `"tool"`) via `orchestrator.modes.get_mode()` and dispatch through it — `orchestrator/modes/` is what makes those modes reachable from a live request rather than tests-only. Five are registered: `tool` (default, LLM calls `call_specialist_agent`), `handoff` (`orchestrator/handoff.py`'s `HandoffBuilder` mesh), `workflow:pre-purchase` (`workflows/pre_purchase.py`'s concurrent fan-out/fan-in), `workflow:return-replace` (`workflows/return_replace.py`'s sequential graph `check → policy-gate → initiate-return → search → discount → finalize`; the gate asks the approval policy and pauses with `ctx.request_info` *before* anything is created, so a rejected return leaves no `returns` row), and `group-chat` (`workflows/group_chat.py`'s round-table debate, two agent-backed panelists then a moderator). The two workflow modes and group-chat resolve a product_id/order_id out of the chat message themselves (a UUID literal if present, else a lookup — `search_products` / the user's most recent order) since neither underlying workflow ever took free text.

**Checkpoints + resume (Phase 1.5).** `PrePurchaseMode` and `ReturnReplaceMode` attach `shared.factory.get_checkpoint_storage()` to every workflow run, wrapped in `shared.checkpoint_storage.RecordingCheckpointStorage` so each save surfaces as its own `kind="checkpoint"` event (MAF's own event stream never mentions a save). `chat.py`'s `_link_run_artifacts()` correlates a run's `usage_logs` row to its checkpoints (`workflow_checkpoints.usage_log_id`) and, when `workflow:return-replace` pauses, writes a `hitl_requests` row carrying MAF's own resume token (`request_id`) and the paused checkpoint. `ReturnReplaceMode.resume()` — reached from `POST /api/orchestration/{run_id}/resume` — builds a *fresh* `Workflow` object (there's no live one to reuse; the one that paused lived in a prior request's process memory) and resumes purely from `checkpoint_id` + `responses={request_id: ReturnDecision(...)}` (a small dataclass: decision, resolution `refund|store_credit|replacement`, amount, note, decider, approval id — it round-trips through `PostgresCheckpointStorage`; MAF's `FileCheckpointStorage` refuses custom types), verified directly to replay correctly through discount and finalize. The approval request carries the full `WorkflowState`, and `_PolicyGateExecutor.on_decision()` restores it on resume, so `return_id` and `replacement_products` survive the pause (they used not to). No decision before the SLA expires resumes the run as a rejection.

`chat_stream()` branches on the resolved mode: `tool` keeps its original direct-agent SSE path (no mode streams token-level deltas the way `_run_agent_native_stream` does), while every other mode streams through `get_mode(...).run()` directly, translating each `OrchestrationEvent` into an SSE frame — `delta` becomes real display text (extracted via `orchestrator.events.delta_text`) when it's present, `node_enter`/`node_exit` becomes `event: node`, `handoff` becomes `event: handoff`, `tool_call` becomes `event: step`, `checkpoint`/`request_info`/`error` get their own named frames. Not every mode streams incremental text — `handoff` does (its "output" events carry `AgentResponseUpdate` content), but the three MAF-workflow modes mostly don't (their executors call `ctx.send_message()`, which produces no delta; the one `ctx.yield_output()` carries a raw state dataclass) — so `_run_mode_task()` falls back to pushing `run_completed`'s full text as a single chunk if nothing streamed incrementally, guaranteeing every mode a non-empty visible response. The web client (`web/src/lib/api.ts::chatStream`) routes any SSE event name outside `step`/`metadata`/`delta`/plain-text to an `onOrchestrationEvent` hook rather than rendering it as chat text — no UI consumes these frames yet (that's Phase 1.6), but the parser had to be fixed to not corrupt the chat bubble with raw JSON once a caller sets `mode` to anything other than `tool`.

### MAF-Native Execution in agent_host.py

`shared/agent_host.py` is a lightweight A2A-compatible FastAPI host. It does **not** implement a custom tool-calling loop — the legacy `_run_agent_with_tools()` / `_run_agent_with_tools_stream()` OpenAI chat-completions loop was removed once MAF-native execution was confirmed compatible with production Azure deployments (see the module docstring). Every request now goes through MAF's own `agent.run()` (blocking) / `agent.run(..., stream=True)` (SSE streaming); the `Agent` object owns its tools, system prompt, and context-provider chain, so `agent_host.py` just threads the A2A request into the right MAF call and (for streaming) forwards chunks over SSE.

### MAF imports

MAF is imported from its public surface (`agent_framework`, `agent_framework.orchestrations`, …),
never from `agent_framework._*`. `agents/python/tests/test_no_private_maf_imports.py` enforces it with
a two-entry allowlist (`encode_checkpoint_value` / `decode_checkpoint_value`, which are not exported
at the pinned version and which `PostgresCheckpointStorage` needs). The old `patch_maf.py`
workaround for the empty-`__init__` packaging bug in core 1.0.0 is gone; it had been a no-op since
the pin moved past it.

### MCP 2.x notes

The two MCP servers (`agents/python/packages/mcp-*`) are `MCPServer` apps; `host="0.0.0.0"` is passed to `streamable_http_app()` (it used to be a constructor argument) so the DNS-rebinding guard does not 421 Docker-network calls, and `AuthSettings(validate_token_resource=False)` is explicit because the vendored `JwksTokenVerifier` checks the audience itself. Client side, `MCPStreamableHTTPTool(http_client=...)` needs an `httpx2.AsyncClient` (see `shared/oauth/service_client.py::build_mcp_http_client`), and `tenacity` is a direct dependency because core imports it lazily for MCP catalog recovery without declaring it outside its `all` extra. `tests/test_mcp_client_tool.py` exercises the real client against a real MCP 2.x server. An MCP tool result's `function_result` carries SDK-supplied `_meta`/`resultType` fields, which are part of the replay key: a tutorial 08 fixture was re-keyed for that (response untouched).

### YAML Prompt Composition System

Prompts are NOT hardcoded strings. `shared/prompt_loader.py` loads from `agents/python/config/prompts/{agent_name}.yaml` and composes: base prompt + grounding-rules (shared) + role-specific instructions + schema context + tool examples.

Shared prompt fragments live in `agents/python/config/prompts/_shared/` (grounding-rules.yaml, schema-context.yaml, tool-examples.yaml).

Each agent's `create_*_agent()` factory calls `get_system_prompt(current_user_role.get())` (which wraps `load_prompt(agent_name, user_role)`) at agent-construction time, and agents are rebuilt on every request (see `orchestrator/routes/chat.py`) — so the composed prompt is genuinely role-aware per request (admin sees different instructions than customer). Earlier revisions built the prompt once at *import* time with a hardcoded default role, which silently defeated the role-specific YAML blocks; that bug is fixed.

### Approvals

One inbox, one decide path, on both stacks over the same tables (Python: `orchestrator/approvals_service.py`; .NET: `Approvals/ApprovalsService.cs`, `Routes/ApprovalRoutes.cs`, `Shared/Approvals/ApprovalRepository.cs`). The routes, JSON and status codes are identical because the web UI calls one API. Full detail is in `docs/approvals.md`; the one known difference (an unattended .NET return cannot be auto-expired by the Python worker) is row 36 of `docs/parity-matrix.md`.

- **Policy, not prompt, decides who approves.** `agents/python/config/policies/approvals.yaml` is an ordered rule list per approval kind (operators `lt/lte/gt/gte/eq/in` only, first match wins) routing to `auto`, `support` or `manager`. Python (`shared/policy/`) and .NET (`ApprovalPolicy.cs`) read the same file and run the same golden cases in `config/policies/testdata/`; a missing fact never satisfies a condition, so `auto` cannot fire on absent evidence. `auto` is *recorded* today, not acted on: the request still goes to a person.
- **State lives in `approvals`** (migration 0006) with an append-only `approval_events` audit; `tool_approval_requests` and `hitl_requests` remain as per-source execution records linked by `approval_id`. Every state change goes through `shared/approvals/repo.py` (`create`, guarded `transition`, `claim`/`release_claim`, `escalate`). The database itself refuses a decider equal to the requester.
- **Deciding** is `POST /api/approvals/{id}/decide` (`approve | reject | request_info`, optional `edits`). Edits are accepted only for the kind's `editable_fields` and can never raise the amount. The legacy resume and admin approve/deny routes call the same function. Every path (tool and workflow, both stacks) re-runs the decider check **after** taking the claim, because an escalation can land between the first check and the claim; an action that already ran but whose recording lost the claim is recorded by a retry and by the timeout handler rather than 409'd or expired; a lost claim is a 409, never a 500.
- **Deadlines are outbox rows.** Entering `pending_review` schedules a reminder and an SLA timer in the same transaction (`shared/outbox.py`). `agents/python/worker/` (compose service `outbox-worker`; `uv run python -m worker.main`) claims rows with `FOR UPDATE SKIP LOCKED` under a lease, retries with capped backoff and parks a row after `OUTBOX_MAX_ATTEMPTS`. A breach escalates `support → manager` once; at the top it applies the kind's `on_timeout`, which for a paused return means resuming it as a rejection. The worker serves both stacks, since they share the database.
- **A merchant proposal is the seller's to decide, never the staff inbox's:** every staff route (decide including `request_info`, answer-info, detail) answers the same 404 a missing id gets for source `proposal`, on both stacks. A reviewer's edit can never raise a line (a restock quantity may only be lowered; a price stays between proposed and starting), enforced at decide time and again in `apply_line`; restock lines have hard limits in `merchant_limits.yaml`. An SLA breach never expires a proposal under a decide in flight. See `docs/merchant-proposals.md`.
- **Identity on execution:** whoever approves, the action runs as the original requester (tools read ContextVars), and the reviewer is recorded separately.

### Merchant proposals (#93, Python only)

Seller markdown/restock suggestions, `docs/merchant-proposals.md`. `shared/merchant/proposals.py` generates them
deterministically (no model), `shared/merchant/execute.py` applies one line at most once (`run_once`, key
`proposal_line:{id}`) after re-running the M-1 guards on live facts; `orchestrator/proposals_service.py` + `routes/proposals.py`
decide a batch line by line. It is **one `approvals` row per batch** (kind `merchant_proposal`, source `proposal`), moved with the
same `repo.claim`/`transition` as every other kind; the requester is the system actor `merchant-ops@system`, so the owning seller
(or an admin) deciding never trips the reviewer-is-not-requester rule. Another seller's proposal is a 404. The M-2 agent and its
eval dataset are not built (they need a key); no `@tool` exists for it, so no fixture is affected. The daily run
(`merchant.daily_run_due`) is off unless `MERCHANT_OPS_SCHEDULE_ENABLED`.

### Event autonomy (#94, Python only)

Budgets for what the system may do on its own when an event arrives, `docs/event-autonomy.md`. `order.shipment_delayed`
(an outbox row; only the admin simulator produces it) is sized into a goodwill credit by a deterministic rule;
`config/policies/autonomy.yaml` (`shared/autonomy/policy.py`, validated whole: the last rule is `needs_approval`, every
autonomous rule carries an `amount lte` ceiling within the budget's per-action cap) picks `auto`, `auto_notify` or
`needs_approval`, and an autonomous tier must also fit every cap (per action, per customer/day, per order, global/day).
`shared/autonomy/engine.py` does the check and the draw in one transaction under a per-(budget, UTC day) advisory lock, so
two events cannot both spend the last of a budget; over a cap it opens an `approvals` row (kind `autonomous_action`, source
`event`, requester the system actor `autonomy@system`, never an auto route) instead of failing. `agent_decisions` and
`store_credit_ledger` are append-only (trigger): a reversal is a new row and a negating ledger entry, and does not return the
day's budget. An event acts once (`run_once` plus a unique `event_key`). A staff approval runs the credit once for the
approval, may only lower the amount, and draws no budget; an SLA breach never expires one mid-decide. **Off unless
`AUTONOMY_ENABLED`**, the simulator additionally needs `AUTONOMY_SIMULATOR_ENABLED`, and no model is called. Every number in
`autonomy.yaml` is a placeholder.

Review-fix rules to keep (details in `docs/event-autonomy.md`): **a reviewer is never the approval's subject**
(`authorize_decider` / `AuthorizeDecider`, a DB CHECK, and the engine never acts alone for a `support`/`admin` beneficiary;
merchant proposals are the one named exemption, `SELLER_DECIDED_SOURCES`). **Lock order: budget, then order, then row locks**;
the credit transaction locks the order and the approval row and pays only while the approval is `pending_review` and the
decide's own claim token holds it. A credited order is `no_action: order_already_credited` (no approval). The engine never
touches the demo tour user (its reset deletes orders that `agent_decisions` would reference). The append-only tables also
refuse `TRUNCATE` when they hold rows; `reset_autonomy_log()` is the one named reset (seeder, `clean_db`). .NET refuses every
decision on an `event` approval (422, "not supported on this stack").

### Return lifecycle (#95, Python only)

With `RETURN_LIFECYCLE_ENABLED` (off by default) an approved return waits for the carrier's first scan instead of finishing
in the approving request, `docs/return-lifecycle.md`. The graph gains `await-carrier-scan` before `finalize`; the pause is a
`hitl_requests` row (`kind='carrier_scan'`, MAF's resume token and checkpoint) plus an outbox timer (`return.scan_timeout`),
opened in one transaction by `shared/return_lifecycle.py::open_scan_wait`. `return.carrier_scanned` (only the admin
simulator `POST /api/admin/returns/{id}/simulate-scan` produces it) resumes the run through
`approvals_service.resume_workflow(kind="carrier_scan")`, the same claim/fence/run-as-requester machinery a reviewer's
decision uses; `worker/return_handlers.py` serves both topics. **A wait is not an approval**: no inbox row, and each path
refuses the other's kind (400). After the scan the run pauses again for **inspection** (`kind='return_inspection'`),
decided by staff through `/admin/returns` (`POST /api/staff/returns/{id}/inspect`), never by the customer; accepting pays out
once through `shared/return_ops.py::resolve_return` (refund, store credit or replacement, the amount only ever lowered), and a
slow inspection only nudges the admins, it never denies the customer. `GET /api/runs/{id}/timeline` feeds the `/runs`
timeline. The flag is read when the graph is built and a checkpoint belongs to the graph that wrote it, so flip it only with
no return paused. .NET: parity row 41, not ported.

### Auth & Identity Flow

- **Roles**: `customer`, `power_user`, `seller`, `support`, `admin`. `require_staff` admits `support` and `admin`; signup always creates a `customer` on both stacks.
- **External requests**: JWT Bearer token validated by `AgentAuthMiddleware` in `shared/auth.py`
- **Inter-agent requests**: `X-Agent-Secret` header (shared secret) + the user's identity, which travels as `A2A_IDENTITY` says (`headers` | `both` default | `token`): the forwarded `X-User-Email` / `X-User-Role` / `X-Session-Id` headers, or a signed, short-lived, audience-bound `X-User-Context` token the orchestrator mints (`shared/identity_token.py` / `Auth/UserContextToken.cs`; same format and key on both stacks, pinned by `config/identity/testdata/user-context.v1.json`). A token that does not verify is refused, never ignored; a header that contradicts a valid one is refused; `token` mode reads no header. The key is HKDF of `A2A_IDENTITY_KEY` else `AGENT_SHARED_SECRET`; symmetric, so a compromised specialist is not stopped. A caller holding only `X-Agent-Secret` is stopped from choosing its user only with `A2A_IDENTITY=token` **and** a distinct `A2A_IDENTITY_KEY` (under the default `both` it can omit the token and forge the headers; `token` without a distinct key logs a startup warning); `jti` is audit-only, there is no replay cache (docs/a2a.md says what it does buy)
- **Signing-key rotation (`AUTH_MODE=oauth`)**: `scripts/rotate_signing_key.py` (logic in `auth_server/cli.py`, which the image carries) inserts a new active key (migration 0014: `not_after`, one-active unique index) and keeps the old one in the JWKS for `AUTH_KEY_RETIRE_AFTER_SECONDS` (default token TTL + 300 s). Validators on both stacks refetch the JWKS once on an unknown `kid`, at most every 30 s (`RotationAwareJWKClient`, vendored in both MCP servers; .NET `JwksKeyProvider.GetSigningKeysForTokenAsync`). Runbook in `docs/security-guide.md`
- **Route/credential matrix**: `docs/auth-route-matrix.md` + `agents/python/tests/auth_route_manifest.json`; `tests/test_auth_route_matrix.py` fails when a route on any surface is added without a classification. Add the route to both
- **Identity propagation**: Auth middleware sets ContextVars (`current_user_email`, `current_user_role`, `current_session_id`) which tools read directly — no parameter passing through the call stack

### A2A 1.0 (both stacks)

Every specialist, Python and .NET, serves two wires; `docs/a2a.md` has the detail. The legacy `/message:send` and `/message:stream` are untouched. Beside them the A2A 1.0 JSON-RPC binding is mounted at `/a2a/v1` with the agent card at `/.well-known/agent-card.json`: Python via the official `a2a-sdk` (1.3.1, spec 1.0; `shared/a2a_server.py`), .NET via the official A2A .NET SDK (`A2A` + `A2A.AspNetCore` **1.0.0-preview4**, spec 1.0, pinned exactly, a preview; `Shared/A2A/Protocol/`). JSON-RPC binding only on both. `A2A_TRANSPORT=legacy|a2a` (default `legacy`) picks what the *caller* speaks, on both orchestrators (Python: `orchestrator/agent.py::_call_over_a2a`, `shared/remote_agent.py`, through `shared/a2a_client.py`; .NET: `A2AClient` behind `A2AProtocolClient`), and any orchestrator can call any specialist over `a2a`: `scripts/a2a-interop.sh` proves both directions across processes.

- **The executor is custom, on purpose, on both stacks.** Python's `SpecialistExecutor` calls `shared.agent_host._run_agent_native_stream` (looked up at call time); .NET's `SpecialistA2AExecutor` calls `AgentHost.RunAgentWithHistoryStreamingAsync`. The messages a specialist's model sees are identical on both wires and no replay fixture changes. Do not swap in `agent-framework-a2a`'s `A2AExecutor` (Python): it builds a MAF session from `contextId`. The conformance tests pin model-input equality.
- **`contextId` is the session id**; the `x-session-id` header is a fallback. Both SDKs invent a `contextId` when the caller sent none and it looks like a session id: Python's generator prefixes invented ids `a2a-ctx-` (`session_id_from_context` ignores them); .NET uses the SDK's `ClientProvidedContextId` flag (`A2AWire.SessionIdFromContext`).
- **Steps, usage and replay misses ride in status-update metadata under three extension URIs** (`steps/v1`, `usage/v1`, `replay-miss/v1`), sent only to a caller that listed them in `A2A-Extensions`. Steps carry an `offset` and the terminal update repeats all of them (the client merges by index). The Python SDK merges status metadata into `Task.metadata`; **the .NET SDK does not**, so `OwnedBoundedTaskStore` republishes it. A protobuf `Struct` writes numbers as floats (`5.0`): .NET wire readers accept whole-valued floats.
- **SDK facts that cost time:** a status update needs an initial `Task` event enqueued first (both SDKs); the first artifact chunk must be `append=false` (Python); the Python SDK client wraps every `httpx` error in `A2AClientError`, so retry decisions go through `shared.a2a_client.root_cause` (the .NET client throws `HttpRequestException` directly: `A2AProtocolClient.NeverRan`); the Python SDK server writes SSE JSON with raw U+2028/U+2029/U+0085, which its client splits lines on (`_SafeJsonRpcDispatcher` escapes them; .NET already does); the .NET SDK's `ITaskStore` passes no caller, so ownership is read from the ambient forwarded identity; the HttpClient the SDK hands a handler has already marked its request as sent, so the guarded .NET handler forwards a copy.
- On the Python orchestrator, `_live` must be set on the very dict `current_steps` holds (as the legacy path does), because `chat.py` drains it from there; a copy sends every step to the browser twice.
- Tasks live in a bounded in-memory store (`BoundedTaskStore` / `OwnedBoundedTaskStore`: the bound is process-wide, reads are scoped to the owner); not a durable record. *Running* tasks are capped per process and per caller (`ActiveTaskLimiter`, `A2A_MAX_ACTIVE_TASKS` / `A2A_MAX_ACTIVE_TASKS_PER_CALLER`; over the cap is a JSON-RPC error, never a queue).
- **A replay miss is only acted on by a replaying orchestrator** (`replay_mode_active()` / `ReplayMiss.IsActive`, on every path of both stacks): elsewhere a 424 or `replay_miss` frame is an ordinary specialist failure and the user's message is kept. Legacy-wire steps are bounded (canonical keys, 50 per frame, 200 per turn).
- **The client only sends to the registry origin** (a card naming another origin is a card problem, no credentials sent, redirects never followed on either wire) and best-effort cancels a streamed task it abandons. The public card lists only tools the `customer` role may call (Python: `requires_role` records its roles on the function; .NET: `[RequiresRole]` metadata cross-checked against every `RoleGuard.Ensure` by `A2ARoleMetadataTests`). Exceptions outside the executor answer a fixed `Internal error`, detail logged.
- **Mixed stacks:** `docker-compose.mixed.yml` + `scripts/e2e-both-stacks.sh --mixed` (`scripts/lib/mixed-env.sh`): `py-net` and `net-py`. Validated with `docker compose config` only. The identity token needs no extra configuration across stacks (the key falls back to the shared secret).

### Conversation History Forwarding

The orchestrator no longer forwards a truncated copy of the conversation history on A2A calls (that "last 10 messages, 500 chars each" window was removed — see the comment in `orchestrator/agent.py::call_specialist_agent`). Instead only the session id travels, via the `x-session-id` header (the message's `contextId` on the A2A 1.0 wire); each specialist rehydrates prior context itself by querying Postgres for the session's messages (`shared/agent_host.py::_rehydrate_history_from_session`) when it needs to handle a follow-up contextually.

Two details this depends on, both of which were missing until #9 — and their absence broke every browser-originated follow-up while every test passed:

- **The orchestrator binds the session id itself.** `current_session_id` is otherwise only ever set from an inbound `x-session-id` header, and the web client does not send one. `orchestrator/routes/chat.py::_bind_session_to_conversation` sets it from the conversation the route just resolved — the conversation id arrives in the request *body*, so this is the only layer that knows it. Anonymous callers are bound to an empty id on purpose: their `conversation_id` is client-supplied and never ownership-checked.
- **Rehydration is scoped to the caller.** The query filters on the caller's own email as well as the conversation id, and refuses outright (with a log line) when no identity was forwarded. Without that, knowing a conversation UUID was enough to read it.

Separately, the orchestrator's own read of *its* conversation's history (for `RunContext.history`, forwarded into every orchestration mode) goes through `shared/session.py::get_history_as_dicts` + `get_history_provider`, not a hand-rolled `SELECT`. Message *writes* stay as each route's own richer `INSERT` (carries `agent_name`/`agents_involved`/`metadata` a generic `HistoryProvider` write doesn't) — see `orchestrator/routes/chat.py`'s module docstring for why a `HistoryProvider` isn't attached as an automatic `context_providers=[...]` hook (verified it would double-write).

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Agent Framework | MAF Python SDK — `agent-framework-core` 1.26.0, `-openai` 1.18.2, `-orchestrations` 1.4.2 (pinned in `agents/python/pyproject.toml` and mirrored in `tutorials/pyproject.toml`), plus `-anthropic` 1.0.0b261112 (a beta; `agents/python` only, the tutorials do not use it), with MCP Python SDK `mcp[cli]` 2.x (`>=2.2,<3`; `FastMCP` is now `mcp.server.mcpserver.MCPServer`, and its client stack is `httpx2`); MAF .NET — `Microsoft.Agents.AI` 1.28.0 (`agents/dotnet/Directory.Packages.props`, shared by the tutorials via `tutorials/Directory.Packages.props`) |
| Agent Communication | A2A Protocol: A2A 1.0 JSON-RPC at `/a2a/v1` on every specialist (`a2a-sdk` 1.3.1 on Python, `A2A` + `A2A.AspNetCore` 1.0.0-preview4 on .NET) and the original `POST /message:send` + `/message:stream` pair (`A2A_TRANSPORT=legacy`, the default); user identity as headers or a signed `X-User-Context` token (`A2A_IDENTITY`) |
| LLM | OpenAI / Azure OpenAI (gpt-4.1) / Anthropic Claude (`claude-sonnet-5-5`, stub-tested only), configurable via `LLM_PROVIDER` env var — plus any OpenAI-compatible endpoint (Ollama, LM Studio, vLLM, OpenRouter, GitHub Models) via `LLM_PROVIDER=openai` + `LLM_BASE_URL` |
| Backend | Python 3.12, FastAPI (orchestrator), Starlette (specialist agents via agent_host) |
| Database | PostgreSQL 16 + pgvector (1536-dim embeddings for text-embedding-3-small) |
| Cache | Redis 8 |
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

# Anthropic (Claude) — Python and .NET; never run against the live API
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-sonnet-5-5   # LLM_MODEL is ignored; also set EMBEDDING_PROVIDER (Claude has no embeddings)

# Any OpenAI-compatible endpoint (Ollama, vLLM, OpenRouter, Gemini's compatible API)
LLM_PROVIDER=openai
LLM_BASE_URL=http://localhost:11434/v1
LLM_API=chat_completions      # default `responses`; needed where there is no /responses route
```

**Per-agent models.** A prompt file may carry `model: {provider, model, temperature}`; `get_chat_client(agent_name)` honours it and `prompt_loader` never lets the block into the composed prompt (a test pins the prompt byte-for-byte). No shipped file sets it.

**Anthropic (Claude) on both stacks** (`LLM_PROVIDER=anthropic`, `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, default `claude-sonnet-5-5` on each). Python uses MAF's `agent-framework-anthropic` (every release is a beta, so it is pinned exactly, `1.0.0b261112`, in the `maf` Dependabot group; it brings `anthropic` 2.x on `httpx2`), through `shared/anthropic_client.py`, which only raises the connector's 1024 `max_tokens` fallback to .NET's 4096. Both stacks are wired and covered by stubbed-transport tests (`tests/test_anthropic_provider.py` on Python); **neither has ever made a live call**. Anthropic has no embeddings API: with no `EMBEDDING_PROVIDER` the stack still starts, warns once, and semantic search uses its full-text arm. Gemini needs no provider code (`LLM_API=chat_completions` + its compatible `LLM_BASE_URL`); see `docs/configuration.md`.

**Embeddings are configured separately** — `EMBEDDING_PROVIDER`, `EMBEDDING_BASE_URL`, `EMBEDDING_API_KEY`, `EMBEDDING_MODEL`, `EMBEDDING_DIM` — defaulting to the chat provider. The configured dimension is checked against the column type at startup; changing it is an explicit operator step (`uv run --project agents/python python scripts/reset_embeddings.py --dim N`), because it discards stored vectors.

### Model pricing

**No price is written in code, on either stack.** Every price is a row in
`agents/python/config/pricing/models.yaml`, read by Python (`shared/cost.py`) and .NET
(`Shared/Cost/CostEstimator.cs`). The file's own header is the how-to; in short:

- **Update or add a model:** copy the numbers from the provider's pricing page in **USD per 1M tokens**
  (the unit providers publish; never convert), set `source` to that page and `as_of` to the date read,
  and `verified: true` only if you read the page yourself. A custom Azure deployment name needs its own row;
  a dated snapshot (`gpt-4.1-2025-04-14`) already uses its base row. Leave `cached_input_per_1m` null unless
  you read the cached rate.
- **Run both stacks' pricing tests** after any edit: `cd agents/python && uv run pytest tests/test_cost.py`
  and `cd agents/dotnet && dotnet test tests/ECommerceAgents.Shared.Tests --filter FullyQualifiedName~CostEstimator`.
  The golden cases in `config/pricing/testdata/` are shared; change them only when the pricing *rules* change.
- **Private prices** (a local model at 0, a negotiated rate) go in a file named by `PRICING_OVERRIDES_FILE`,
  never in the shipped file. `PRICING_FILE` replaces the shipped file. Both are read by host-run processes;
  containers read the pricing directory mounted read-only at `/pricing` (restart after editing, no rebuild), so
  `PRICING_OVERRIDES_FILE` must be a path under `/pricing`.
- **An unlisted model's cost is unknown (null), never a guess.** Do not reintroduce a fallback price: the old
  gpt-4.1 fallback made every Ollama, Gemini and custom-deployment run report invented dollars. Under
  `COST_BUDGET_MODE=enforce` with a ceiling, an unpriced turn stops the run (fail closed).
- `tests/test_cost.py::test_no_price_is_written_in_code` fails if a float price literal appears in `shared/cost.py`.

`LLM_API` is Python-only: the .NET stack always uses Chat Completions. Under `LLM_PROVIDER=replay`
no model is called at all — that is what CI, the eval smoke suite and the tutorials' keyless path use.

## Database

- Schema: `docker/postgres/init.sql` is the frozen baseline (34 tables; the 11 added by migrations bring it to 45); every change since is a numbered file in `docker/postgres/migrations/` (0002–0015 today: approval claims, refresh tokens, lower-cased user emails, refresh-family expiry, approvals/outbox/notifications, usage cost columns, embedding model, merchant proposals, expiry of pre-policy return approvals, worker leases, event autonomy, return lifecycle columns, signing-key rotation, usage price source) applied by `scripts/migrate.py` (the seeder, `dev.sh`/`dev.ps1` and every test fixture run it). Never edit an applied migration — the checksum guard fails the run; add a new one. The runner applies whatever is unapplied in numeric order, so a lower number arriving late is fine.
- All queries use parameterized `$1, $2` syntax (asyncpg)
- All user-facing queries filter by `user_email` or `user_id`
- Embeddings stored as `vector(1536)` with ivfflat cosine index
- Seeder (`scripts/seed.py`) is deterministic (`random.seed(42)`) — reproducible demo data

## Testing & Evals

Five traps here cost more time than anything else in the repo.

**Playwright: wait on the composer's stop icon, never on status text.** A streamed
turn is finished when the submit button stops showing the stop icon — not when
"Routing to specialists…" disappears, which happens mid-turn. Waiting on the text
lets the next click land while the composer is still disabled, so it silently
no-ops and the assertion fails somewhere unrelated. See the helpers in
`web/e2e/chat-followup-context.spec.ts` and `web/e2e/chat-all-users.spec.ts`
(the two currently use different selectors for the same button; prefer
`button[aria-label="Stop"], button:has(svg.lucide-square)`).

**Replay fixtures invalidate on tool-output changes, not just prompt edits.**
`evals/fixtures/replay` is keyed by a hash of the exact request — every message
plus the tool schemas (`shared/replay_client.py`). A tool message is part of that,
so changing what a tool *returns* changes the key and the fixture misses, which the
harness reports as an infrastructure failure rather than a bad answer. Re-record
after any change to a dataset, a prompt, a tool schema, or a tool's output shape;
the recipe is in `evals/fixtures/README.md`.

**Two eval runs on one machine collide.** The specialists bind fixed ports 8081–8085, so a second
`scripts/replay-evals.sh` (or anything else on those ports) makes the first report missing fixtures
that are not missing. Serialize runs — `flock <lockfile> scripts/replay-evals.sh` — and never
`pkill` services by name. The same symptom appears when re-seeding a database that was already
seeded: start from a fresh one.

**`fixture_drift` exit 0 does not prove the fixtures still match.** It diffs stored requests against
what the installed MAF would serialise, but it cannot see values MAF generates at run time. Core 1.21
began stamping a random `af-call-<uuid>` id on every function call, which lands in the next turn's
request: the drift tool reported clean while every post-tool-call fixture missed. `replay_client`
now strips those ids from the key. After any MAF bump, `scripts/replay-evals.sh` is the gate; the
drift tool only tells you which kind of repair is needed.

**The `full` eval suite spends real money and is human-dispatch only.** It runs on
`workflow_dispatch` alone, never a schedule. Nothing in this repo may put a live
LLM key behind an automatic trigger — keep it that way when adding jobs to
`.github/workflows/evals.yml`.

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

- Create custom tool registries — use MAF's built-in `@tool`
- Write raw OpenAI function-calling loops — use the existing `agent_host.py` pattern
- Use `pip` or `poetry` — use `uv` for Python
- Use `npm` or `yarn` — use `pnpm` for Node
- Skip type hints on any function
- Use `requests` — use `httpx` for async HTTP
- Hardcode prompts in Python — use YAML config in `agents/python/config/prompts/`
- Pass user identity as function args — use ContextVars from `shared/context.py`
