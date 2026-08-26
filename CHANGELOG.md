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

## [1.2.0] - 2026-08-26

Two things in here were found by running the software rather than reading it, which is becoming
the pattern for this project. The orchestration-mode failure had been shipping in every container
image for as long as the image has had its current shape.

### Fixed

- **Three of the five orchestration modes were dead in every Docker image.**
  `workflow:pre-purchase`, `workflow:return-replace` and `group-chat` returned "I apologize, but I
  encountered an issue processing your request" — the same 82 characters regardless of the prompt,
  in under 10ms. The Dockerfile copied `shared/`, `config/` and `${AGENT_NAME}/` and nothing else,
  so `workflows/` was absent, as were the four specialist packages the orchestrator's workflow
  modes import in-process. Every containerised deployment was affected, including plain
  `docker compose up`, not only the published images.
  Nothing caught it: E2E is deliberately not run in CI, the eval harness runs in-process where
  those packages are on `sys.path` regardless of image contents, and the image smoke-test imports
  `<agent>.main` only — every one of these imports is lazy, inside the mode, so the module imports
  cleanly and fails at request time.
- **A flaky .NET test could block image publishing.**
  `ChatRoutesTests.StreamAsync_SecondTurn_ForwardsFullPriorHistoryToAgent` intermittently threw
  `NullReferenceException` from `DefaultHttpContext.get_RequestAborted()`. `PostAsJsonAsync`
  returns on response headers and that endpoint streams, so the follow-up request's handler was
  still running when the test disposed the TestServer, and it then read a recycled pooled context.
  The response is now drained, and the handler captures its cancellation token once at entry rather
  than dereferencing a pooled context four times late in a stream. It matters beyond a red X:
  `publish-main` gates on the .NET suite, so the flake blocked releases.
- **A pull-request push could cancel an in-flight image publish.** `workflow_dispatch` defines no
  `tag_mode` input, so the concurrency-group fallback collapsed a manual publish into the same
  bucket as a pull-request build. A push then cancelled a publish that had already pushed several
  of the ten images, leaving the tag half-updated.

### Added

- **Hybrid product search.** `search_products` split the query into words and ANDed a `%word%`
  `ILIKE` per word, then ordered by rating alone, so "noise cancellation" never matched "noise
  cancelling" and a single absent word emptied the result set. It is now a weighted generated
  `tsvector` (name=A / brand=B / description=C) behind a GIN index, OR-joined and ordered by
  `ts_rank`. `semantic_search` became hybrid with it: the vector and full-text arms run as separate
  ranked CTEs fused by Reciprocal Rank Fusion. Applied to the native tool, `mcp-product` and .NET,
  which closed a real `MCP_ENABLED` divergence.
  **Upgrading an existing database:** the `tsvector` column ships in `docker/postgres/init.sql`,
  which Postgres only runs on an empty data directory — either `./scripts/dev.sh --clean` or apply
  it in place, see [Troubleshooting](docs/troubleshooting.md#products-search_vector-does-not-exist).
- **A one-command demo that pulls instead of building.** `docker-compose.demo.yml` plus
  `./scripts/dev.sh --demo` (`-Demo` on PowerShell) take a first run from roughly twelve minutes to
  roughly one. Measured on a clean machine: 37s to pull all ten images, 24s to a healthy stack.
  `IMAGE_TAG` overrides the tag for testing `:main` or a pinned version.
- **Container images published to GHCR for all ten services, gated on the test suite.** A push to
  `main` publishes `:main` and `:sha-<7>`; a version tag publishes `:vX.Y.Z` and `:latest` after a
  full test re-run and a manual approval. Images are `linux/amd64` and `linux/arm64`, so Apple
  Silicon runs natively rather than under QEMU.
- **A release pipeline.** `.github/workflows/release.yml`, `scripts/bump_version.py` (with a
  `--check` mode CI uses to block version drift), `CHANGELOG.md`, and
  [`docs/releasing.md`](docs/releasing.md). Before this, a semver tag published images with no
  dependency on any test job, so a tag on a red commit shipped.
- **A retention policy for the registry.** `package-cleanup.yml` runs weekly, keeps the most recent
  20 versions per package and never touches `:latest`, `:main` or any `:vX.Y.Z`.
- **`llms.txt`, `llms-full.txt` and `robots.txt`**, generated from the same page set the site is
  built from. Over a 14-day window chatgpt.com sent more traffic to this repository than Google or
  Bing individually, and the site published nothing shaped for that.
- **`.env.minimal`** — one variable, now the quick-start default, with `.env.example` (210 lines)
  demoted to reference material. [`docs/configuration.md`](docs/configuration.md) documents how one
  `.env` reaches containers, host-run Python, the frontend and the .NET stack, which do not all read
  it the same way — notably, containers do not read it at all.
- **Community surface** — `SECURITY.md`, a code of conduct, issue and pull-request templates, and
  Discussions.
- **An orchestration-mode benchmark harness** (`evals/benchmark_modes.py`). It drives the HTTP API
  rather than calling modes in-process, so a run exercises auth, guardrails, grounding and usage
  logging rather than a copy of them. Not wired into CI: it costs real tokens and cannot run under
  `LLM_PROVIDER=replay`.
- **A Playwright recording spec** for the demo clip (`web/e2e/demo-recording.spec.ts`), so the clip
  can be re-recorded after a UI change instead of decaying.

### Changed

- **`build-images.yml` can no longer publish on its own initiative.** Its `push` and tag triggers
  are gone; publishing is caller-driven through `workflow_call`, so the gate lives with the caller.
  The image matrix covers ten images rather than six — `auth-server`, `mcp-product`,
  `mcp-inventory` and `frontend` had never been built by CI at all.
- **The README is 247 lines rather than 740.** The material a senior reader wants — grounding,
  idempotency, HITL, rate limiting, tracing — was in a section starting at line 624. Nothing was
  removed without a destination: [`docs/roadmap.md`](docs/roadmap.md) and
  [`docs/demo-guide.md`](docs/demo-guide.md) are new.
- **The tutorial index no longer describes finished chapters as drafts.** Thirty-four rows read
  "Draft" while the chapters were complete and gated in CI; that vocabulary described the blog
  posts. The table is now generated from what is on disk, which also exposed four false claims in
  the surrounding prose.

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

[Unreleased]: https://github.com/nitin27may/e-commerce-agents/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/nitin27may/e-commerce-agents/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/nitin27may/e-commerce-agents/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/nitin27may/e-commerce-agents/releases/tag/v1.0.0
