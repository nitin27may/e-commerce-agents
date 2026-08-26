# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Entries are written by hand rather than generated from commits. What matters is what changed for
someone using or reading this repository, which is a judgement call — and several of the entries
below say "this never worked", which no commit-message generator would ever produce.

Releases are cut with `scripts/bump_version.py` and `.github/workflows/release.yml`. See
[`docs/releasing.md`](docs/releasing.md).

## [Unreleased]

### Added

- Container images published to GHCR for all ten services, gated on the test suite. A push to
  `main` publishes `:main` and `:sha-<7>`; a version tag publishes `:vX.Y.Z` and `:latest`, after
  a full test re-run and a manual approval.
- `.github/workflows/release.yml` — the release pipeline. Before it, a semver tag published images
  with no dependency on any test job, so a tag on a red commit shipped.
- `scripts/bump_version.py` — one command to set the version in `pyproject.toml`,
  `package.json` and `Directory.Build.props`, with a `--check` mode that CI uses to block drift.
- [`docs/releasing.md`](docs/releasing.md) and [`docs/configuration.md`](docs/configuration.md).

### Changed

- `build-images.yml` can no longer publish on its own initiative. Its `push` and tag triggers are
  gone; publishing is caller-driven through `workflow_call`, so the gate lives with the caller.
- The image matrix covers ten images rather than six — `auth-server`, `mcp-product`,
  `mcp-inventory` and `frontend` were never built by CI.
- Published images are multi-architecture (`linux/amd64` and `linux/arm64`). Readers on Apple
  Silicon previously got QEMU emulation. Pull-request builds stay amd64-only, because `load: true`
  cannot load a multi-platform image and the import smoke-test depends on it.

## [1.1.0] - 2026-08-25

The theme of this release, stated plainly: **five times running, the reported problem was smaller
than the actual one**, and each time the difference was found by running something rather than
reading it. Two of the five were found only because a CI gate had just been switched on.

### Fixed

- **Follow-up questions keep their context.** Specialists received *no* conversation history on any
  browser-originated turn, on the Python stack, deterministically. The web client never sent
  `x-session-id`, so rehydration short-circuited before the database and without logging. It read
  as model nondeterminism for weeks because the orchestrator sometimes inlined context into the
  specialist message and sometimes didn't. Fixed on both stacks, with the rehydration query now
  scoped to the caller's own conversation.
- **Semantic search actually works.** It was dead under `LLM_PROVIDER=replay`, so no CI run ever
  exercised pgvector — and underneath that sat a production bug: the IVFFlat index is created on an
  empty table, so it had no centroids and returned unrelated products at similarity 0.000 where an
  exact scan returned the right one at 0.420.
- **Promotions apply.** `promotions.rules` is untyped JSONB and the seeder wrote different key
  names than the reader read, so bundles contributed £0 on every cart, buy-X-get-Y crashed, and
  flash sales silently never matched. No promotion had ever applied correctly, in any environment.
- **.NET runs appear in Aspire's GenAI view.** .NET emitted `agent.run`/`chat` where the convention
  Aspire selects on is `invoke_agent`, so the dashboard looked empty on that backend while working
  normally on Python. Npgsql instrumentation, a meter provider, a log bridge and
  session/conversation enrichment landed with it.
- **The docs site is indexable.** All 85 pages shared one meta description. Now per-page
  descriptions, keywords, `TechArticle` JSON-LD, `lastmod`, a social image, and an accessible title
  on every one of the 71 diagrams.

### Added

- **A CI gate for the .NET tutorials.** No job had ever built any of the 31 tutorial `.csproj`
  files. Turning the gate on immediately found chapter 08 entirely broken.
- **Workflow resume on both stacks.** The pause → badge → Approve → resume loop is real on .NET as
  well as Python. On .NET the resume rebuilds the workflow from a Postgres checkpoint rather than
  holding the paused run in memory, so it survives an orchestrator restart, and the pending row is
  claimed before the workflow executes so a double-click cannot release two refunds.

## [1.0.0] - 2026-08-20

First release. A multi-agent e-commerce platform with two complete backends behind one frontend.

### Added

- **Six specialist agents over A2A** — product discovery, order management, pricing and promotions,
  review sentiment, inventory and fulfillment, plus the orchestrator front door.
- **A full .NET / C# backend** — the same orchestrator and five specialists plus an MCP host, the
  same A2A protocol and PostgreSQL schema, idiomatic .NET throughout. Eight test projects, 450 test
  methods. Reached parity on the shipped surface through a nine-PR effort, gated by a dual-backend
  Playwright suite rather than a checklist.
- **Orchestration modes, live** — the same question answered by a tool router, a handoff mesh, two
  workflow graphs or a group-chat round table, selected per request from the composer. The graph
  animates node-by-node from real SSE events, and "compare modes" runs one prompt through several
  and reports latency side by side.
- **Agent evaluators** — eval sets (precision@k, recall@k, answer faithfulness, tool-call
  correctness) across all six specialists. The `smoke` job gates every pull request using committed
  replay fixtures, so it needs no API key and costs nothing. The harness drives the *production*
  path, so guardrails, sanitization and HITL gates are exercised rather than bypassed.
- **Server-side grounding** — the model's claims are checked against Postgres before the answer
  leaves. Product and order ids in card blocks are verified to exist and to carry the quoted price.
- **Prompt injection prevention** — `shared/guardrails/` in the middleware stack for all agents,
  observe-first by default.
- **Human-in-the-loop with checkpoint resume** — a workflow suspends on its HITL gate and resumes
  from a real Postgres checkpoint.
- **Idempotency on money-moving actions** — an `idempotency_keys` table plus an `@idempotent`
  decorator on returns, refunds and checkout. Approval writes fail *closed*.
- **Resilience and rate limiting** — bounded retries with jittered backoff and a per-endpoint
  circuit breaker on every A2A call, and a Redis sliding-window limiter on both chat routes.
- **Distributed tracing** — OpenTelemetry throughout with GenAI semantic conventions, a Langfuse
  sink, and `trace_id` correlated into `usage_logs`. Spans nest correctly across A2A hops.
- **MCP data-access layer** — `mcp-product` and `mcp-inventory` as standalone, independently
  publishable packages in a uv workspace, usable by any MCP client.
- **Self-hosted OAuth2 authorization server** — an opt-in `AUTH_MODE=oauth` path with the token
  issuer inside this repo. RS256 with a JWKS endpoint, client-credentials service tokens replacing
  the static A2A shared secret, and both MCP servers hardened into OAuth 2.1 resource servers.
- **Generative UI** — every agent response rendered by the shape of its data: cards, tables,
  charts, badges. A malformed payload renders nothing rather than falling back to raw JSON.
- **Session memory and context persistence** — `store_memory` / `recall_memories` surfaced to the
  orchestrator via context providers.

[Unreleased]: https://github.com/nitin27may/e-commerce-agents/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/nitin27may/e-commerce-agents/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/nitin27may/e-commerce-agents/releases/tag/v1.0.0
