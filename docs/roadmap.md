# Roadmap

Where this project is, what shipped, and what is deliberately not done yet.
Generated from the same material that used to sit at the bottom of the README,
where it was below line 600 and effectively unread.

The per-release record is in [CHANGELOG.md](https://github.com/nitin27may/e-commerce-agents/blob/main/CHANGELOG.md).
The working list, including gaps this page does not claim to cover, is in
[`.claude/plans/remaining-work.md`](https://github.com/nitin27may/e-commerce-agents/blob/main/.claude/plans/remaining-work.md).

## Project status

**This is v1, and both backends are live.** Each runs end-to-end: an orchestrator plus five specialist agents, auth, telemetry, and a full Next.js frontend that either backend can serve.

The frontend is a **public, agentic e-commerce storefront**: anyone can browse the catalog, search, and use the AI shopping assistant (`/shop`) without an account — product discovery is served anonymously — while account flows (cart checkout, orders, tracking, returns) require sign-in. A built-in **agent-activity timeline** surfaces the multi-agent routing (orchestrator → specialist → tool) live in chat, backed by OpenTelemetry → .NET Aspire. Light/dark theming throughout.

The **.NET / C# backend** at [`agents/dotnet/`](../agents/dotnet/) is a real implementation, not a demonstration slice: it serves the same frontend, the same database and the same prompts as [`agents/python/`](../agents/python/). Parity is enforced rather than asserted — `web/e2e/orchestration-parity.spec.ts` drives one frontend against both backends and asserts *presence* of each capability, because the earlier suite went green against a .NET stack that was missing four whole features. Remaining differences are listed in [`docs/parity-matrix.md`](parity-matrix.md).

---


## What has shipped

This is v1.1. Both backends are live and stable. Remaining work is consolidated in
[`.claude/plans/remaining-work.md`](https://github.com/nitin27may/e-commerce-agents/blob/main/.claude/plans/remaining-work.md) — including the gaps
this section does not claim to cover.

One pattern is worth stating, because it shaped most of v1.1: **five times running, the reported
problem was smaller than the actual one**, and each time the difference was found by running
something rather than reading it. "Follow-ups occasionally lose context" was deterministic and total.
"`optimize_cart` divides by zero" was *no promotion had ever worked*. Two were found only because a
gate had just been switched on — which is why the gates below come before the content work.

Legend: `- [x]` shipped · `- [ ]` planned or in progress.

### Shipped in v1

- [x] **Agent evaluators** — scripted eval sets (precision@k, recall@k, answer faithfulness, tool-call correctness) across all six specialists, run against the seeded catalog. `.github/workflows/evals.yml` runs two jobs. **`smoke` gates every pull request**: deterministic scorers only, driven by committed replay fixtures under `LLM_PROVIDER=replay`, so it needs no API key, costs nothing, and fails the PR when a suite regresses more than 5% against its committed baseline. **`full`** runs weekly on a schedule (and on demand) with a real key and the LLM judge. The harness drives the *production* path — `evals/harness.py` runs the same orchestration modes a real request does, so guardrails, sanitization and HITL gates are exercised rather than bypassed.
- [x] **Prompt injection prevention** — `shared/guardrails/` wired into the middleware stack for all agents. Enabled by default (`GUARDRAILS_ENABLED=true`); runs in observe-first mode (`GUARDRAILS_FAIL_OPEN=true`) — flags and logs injections. Set `GUARDRAILS_BLOCK_ON_INJECTION=true` to enable hard blocking once false-positive rates are measured in your environment.
- [x] **Session memory & context persistence** — `store_memory` / `recall_memories` tools in `shared/tools/memory_tools.py`, surfaced to the orchestrator via `shared/context_providers.py`. Per-user preferences, recent intents, and history make follow-ups feel continuous.
- [x] **Full .NET / C# backend** — the same orchestrator and five specialists plus an MCP host, the same A2A protocol and PostgreSQL schema, idiomatic .NET throughout. Eight test projects, 450 test methods (~500 cases counting `[Theory]` data). Reached parity on the shipped surface through a nine-PR effort covering the shared tool library, orchestration modes, normalized SSE events, server-side grounding, rate limiting, cost estimation and a HITL claim-before-execute fix — gated by a dual-backend Playwright suite rather than a checklist. See [`agents/dotnet/`](../agents/dotnet/) and [`docs/parity-matrix.md`](parity-matrix.md).
- [x] **Distributed tracing across every agent** — OpenTelemetry throughout (`shared/telemetry.py`), GenAI semantic conventions, a Langfuse sink, and `trace_id` correlated into `usage_logs` so a row in the admin usage table links back to its trace. Spans nest correctly across A2A hops, so one chat turn reads as a single tree in the [Aspire Dashboard](http://localhost:18888). The dashboard itself runs stock — this repo ships no pre-built views.
- [x] **MCP data-access layer (2 servers)** — `mcp-product` (:9000) and `mcp-inventory` (:9001) are standalone, independently publishable Python packages (`packages/mcp-product`, `packages/mcp-inventory`) in a uv workspace. They expose product and inventory data over the MCP streamable HTTP transport (FastMCP). Flag-gated via `MCP_ENABLED`; `product-discovery` and `inventory-fulfillment` swap their direct-asyncpg `@tool` set for `MCPStreamableHTTPTool` with no behavior change. Any MCP-compatible client — Claude Desktop, Cursor, LangGraph — can use them without this codebase. See [MCP Integration](mcp-integration.md).
- [x] **Self-hosted OAuth2 Authorization Server** — opt-in `AUTH_MODE=oauth` path with the token issuer living *inside* this repo (`agents/python/auth_server/`, built on `authlib`), so login and every service call are genuinely OAuth2-compliant with no external identity provider or cloud dependency. RS256 signing with an AS-generated keypair and a JWKS endpoint; user login via the resource-owner-password grant brokered by the orchestrator (the browser keeps its email/password form); client-credentials service tokens replacing the static A2A shared secret; and both MCP servers hardened into OAuth 2.1 resource servers (audience/scope validation, `.well-known/oauth-protected-resource`, `WWW-Authenticate` challenge) — Python and .NET parity throughout. Fully additive — `AUTH_MODE=local` (self-issued JWT + shared secret) stays the zero-config default, so the OpenAI-key-only quick-start is unaffected. Verified end to end against a live stack: real browser login and chat session on AS-issued tokens, role-gated routes, inter-agent and MCP calls authenticated purely on OAuth scopes (no shared secrets), and cross-scope/cross-resource token rejection — both stacks, including the .NET MCP host validated against the real running auth-server. See [`.claude/plans/enhancements/10-oauth-authorization.md`](https://github.com/nitin27may/e-commerce-agents/blob/main/.claude/plans/enhancements/10-oauth-authorization.md).

- [x] **Server-side grounding** — the model's claims are checked against Postgres before the answer leaves. Product and order ids in card blocks are verified to exist and to carry the quoted price; a fact-check badge reports how many claims were verified. `GROUNDING_MODE` is `annotate` by default (`shared/grounding/`, `Shared/Grounding/`).
- [x] **Orchestration modes, live** — the same question can be answered by a tool router, a handoff mesh, two workflow graphs or a group-chat round table, selected per request from the composer. The graph animates node-by-node from real SSE events, and "compare modes" runs one prompt through several and reports latency side by side.
- [x] **Idempotency on money-moving actions** — an `idempotency_keys` table plus an `@idempotent` decorator on returns, refunds and checkout, so a resubmitted approval cannot double-execute. Approval writes fail *closed*.
- [x] **Resilience and rate limiting** — bounded retries with jittered backoff and a per-endpoint circuit breaker on every A2A call (`shared/http_resilience.py`, mirroring the .NET Polly pipeline that led here), and a Redis sliding-window limiter on both chat routes, keyed by user and by IP for anonymous traffic.
- [x] **Generative UI** — every agent response is rendered by the shape of its data: cards, tables, charts, badges. An unrecognized or malformed payload renders nothing rather than falling back to raw JSON.

### Shipped in v1.1

- [x] **Follow-up questions keep their context** — specialists received *no* conversation history on any browser-originated turn, on the Python stack, deterministically. The web client never sent `x-session-id`, so rehydration short-circuited before the database and without logging. It read as model nondeterminism for weeks because the orchestrator sometimes inlined context into the specialist message and sometimes didn't. Fixed on both stacks, with the rehydration query now scoped to the caller's own conversation.
- [x] **.NET runs appear in Aspire's GenAI view** — .NET emitted `agent.run`/`chat` where the convention Aspire selects on is `invoke_agent`, so the dashboard looked empty on that backend while working normally on Python. Npgsql instrumentation, a meter provider, a log bridge and session/conversation enrichment landed with it.
- [x] **The .NET tutorials have a CI gate** — no job had ever built any of the 31 tutorial `.csproj` files. Turning the gate on immediately found chapter 08 entirely broken.
- [x] **Semantic search actually works** — it was dead under `LLM_PROVIDER=replay` (so no CI run ever exercised pgvector), and underneath that sat a production bug: the IVFFlat index is created on an empty table, so it had no centroids and returned unrelated products at similarity 0.000 where an exact scan returned the right one at 0.420.
- [x] **Promotions apply** — `promotions.rules` is untyped JSONB and the seeder wrote different key names than the reader read, so bundles contributed £0 on every cart, buy-X-get-Y crashed, and flash sales silently never matched. No promotion had ever applied correctly.
- [x] **The docs site is indexable** — all 85 pages shared one meta description. Now per-page descriptions, keywords, `TechArticle` JSON-LD, `lastmod`, a social image, and an accessible title on every one of the 71 diagrams.

### Shipped in v1.2

- [x] **Three of the five orchestration modes were dead in every Docker image** — `workflow:pre-purchase`, `workflow:return-replace` and `group-chat` returned the same 82-character apology regardless of the prompt, in under 10ms. The Dockerfile copied `shared/`, `config/` and `${AGENT_NAME}/` and nothing else, so `workflows/` was simply absent. Every containerised deployment was affected. Nothing caught it: the eval harness runs in-process where those packages are on `sys.path` regardless of image contents, and the image smoke-test imports `<agent>.main` only — every one of the missing imports is lazy, inside the mode, so the module imports cleanly and fails at request time.
- [x] **Hybrid product search** — `search_products` split the query into words and ANDed `%word%` patterns, so a natural phrase matched nothing. Replaced with Postgres full-text search plus reciprocal-rank fusion against the vector index.
- [x] **A release pipeline, and images to publish with it** — container images for all ten services on GHCR gated on the test suite, `release.yml`, `scripts/bump_version.py`, and a weekly registry retention policy. Plus `docker-compose.demo.yml`, which pulls rather than builds.
- [x] **The site is legible to machines** — `llms.txt`, `llms-full.txt` and `robots.txt`, generated from the same page set the site is built from, so they cannot drift from it.
- [x] **An orchestration-mode benchmark harness** (`evals/benchmark_modes.py`) — drives the real HTTP API rather than calling modes in-process, so it exercises auth, guardrails, sanitization and grounding along the way.

### Shipped in v1.3

- [x] **Tutorial .NET coverage** ([#20](https://github.com/nitin27may/e-commerce-agents/issues/20)) — done. Every chapter that ships code now ships both languages, both tested in CI: 334 .NET tests across 31 projects (was 47 across 11). The one remaining gap is chapter 20b, and it is Microsoft's: `Microsoft.Agents.AI.DevUI` is prerelease-only. The status table in `tutorials/README.md` is now generated from disk by `scripts/check_tutorial_coverage.py` and gated in CI, so it cannot drift again.
- [x] **Composer UX** ([#4](https://github.com/nitin27may/e-commerce-agents/issues/4)) — done. The six always-visible mode chips took the composer's top third to expose a control most turns never touch; they collapse into one toolbar picker. Suggested prompts are now derived from the assistant's last message rather than being the same four canned prompts after every turn — keyed off the message's typed card payload first (a ```product fence means the user is looking at products, whatever the prose says) and its closing question second, with the old static list as the fallback. No LLM call: it is a pure function over text the client already has.
- [x] **In-chat approval card** — done. The pause-and-resume loop already worked on both stacks, but the only control that could release a pause lived on `/runs`, so the user who caused it had no way to act without knowing a separate page existed. The blocker was that no streaming client ever learned the run's id: it *is* the `usage_logs` row, created during persistence, after `event: metadata` has gone out. Both stacks now emit `event: run` (`{run_id, pending_approval}`) once persistence lands and before `[DONE]`, and the chat thread renders Approve/Reject inline. Fixing this also surfaced that .NET wrote `[DONE]` before persisting — the race Python fixed as #9 — so the two stacks disagreed on whether a finished turn was durable.
- [x] **Cost metrics as first-class counters** — done. `shared/telemetry.py` had exposed `get_meter()` since telemetry was wired up and nothing had ever called it, so every metric on the dashboard came from MAF's or FastAPI's instrumentation and the one number this application knows — what a run costs — existed only as a log line. Both stacks already price every turn to enforce a budget ceiling; that estimate is now emitted as `ecommerce.llm.cost.usd`, with tokens split by direction beside it, because cost is derived from tokens through a hand-edited price table and only the raw counts say which of the two moved. Same instrument and meter names on Python and .NET, so one dashboard covers both. Nothing user-scoped is attached to the attributes.
- [x] **Streaming tool calls end-to-end** — done. Both stacks batched their timeline steps until after the last text chunk, so the timeline snapped into place once the answer had finished writing — precisely when it stops being useful. In a MAF tool loop the tools resolve first and the prose narrating them comes second, so both specialist hosts now drain steps before each chunk and both orchestrators forward them live. Not done, deliberately: rendering cards from those payloads as well, since the answer text already carries ```product fences for the same data and a client drawing from both would show every card twice.

### In Progress

- [ ] **.NET eval suite** — 6 of 7 datasets are ported and the enabling work is done (record-on-miss, and an embedding seam without which product-discovery could not start in replay mode at all). What remains is the recording run, the baselines, and the CI job. `red_team` needs its own evaluator and is tracked separately.

---

### Search & Retrieval

`search_products` is now Postgres full-text search over a weighted `tsvector`, and `semantic_search` fuses that lexical arm with the pgvector cosine arm — see *Hybrid product search* under Shipped in v1.1. What is left here is the shape of the filter surface, not the retrieval itself:

- [ ] **Typed filter DSL** — replace the flat parameter list on `search_products` with a structured `ProductFilters` Pydantic model (category, price, brand, sort). Keeps SQL parameterized and safe.

> **Upgrading an existing database.** The `tsvector` column ships in `docker/postgres/init.sql`,
> which Postgres only runs on an empty data directory. Either `./scripts/dev.sh --clean` (drops all
> local data) or apply it in place — see [Troubleshooting](troubleshooting.md#products-search_vector-does-not-exist).

Text-to-SQL was considered and rejected: `user_email`/`user_role` scoping via ContextVars means dynamic SQL would bypass that contract. The typed filter DSL gives the model flexibility at the boundary while keeping SQL generation server-side and auditable.

---

### MCP as the Agent Data-Access Layer

| Server | Port | Domain |
|--------|------|--------|
| `mcp-product` | 9000 | Product search, details, comparison, trending, price history |
| `mcp-inventory` | 9001 | Stock levels, warehouses, shipping, carriers |

Those two are the **Python** stack. The .NET stack serves both domains from a single host (`ECommerceAgents.Mcp`) on **:9001** — there is no :9000 in `docker-compose.dotnet.yml`.

Both are standalone publishable packages in a uv workspace (`packages/mcp-product`, `packages/mcp-inventory`). Start them with:

```bash
docker compose --profile mcp --profile agents up

# then set in .env
MCP_ENABLED=true
MCP_PRODUCT_SERVER_URL=http://localhost:9000/mcp
MCP_INVENTORY_SERVER_URL=http://localhost:9001/mcp
```

See [MCP Integration](mcp-integration.md) for the full setup guide, tool coverage table, external client examples (Claude Desktop, LangGraph), and publishing instructions.

Planned:

- [ ] **External integration surface** — publish `ecommerce-mcp-product` and `ecommerce-mcp-inventory` to PyPI so any MCP-compatible client can `pip install` and run them against any PostgreSQL database without this codebase.
- [ ] **Eval gate** — run each eval dataset twice (native tools vs MCP path) and fail CI if the MCP run scores below the native baseline.

---

### Planned — Platform & Observability

- [ ] **Prompt caching** — cache system prompts and tool schemas per agent to reduce per-request token cost on repeated specialist invocations.

---

