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

Azure pre-work. Two application-code blockers that would each have stopped a deployment, neither of
them about Azure services. Both are contract changes any additional backend inherits, which is why
they landed before the infrastructure work rather than during it.

### Changed

- **The frontend no longer knows its backend's address.** `NEXT_PUBLIC_API_URL` was inlined into
  the client bundle at build time, and a Container Apps FQDN does not exist until provisioning — so
  the image would have had to be rebuilt after deploying, which is what makes a one-command deploy
  impossible. The browser now calls its own origin and `web/src/app/api/[...path]/route.ts`
  forwards `/api/*` to `ORCHESTRATOR_URL`, a server-side variable read per request. One image runs
  in every environment, the orchestrator needs no public ingress, and CORS is gone.
  `NEXT_PUBLIC_API_URL` remains as a direct-call escape hatch.

  Two things only a running server caught. A `rewrites()` entry in `next.config.ts` cannot do this:
  Next evaluates `rewrites()` during `next build` and bakes the destination into
  `routes-manifest.json`, which is the same build-time problem in a new place. And *deleting* the
  browser's `accept-encoding` before forwarding is not enough, because undici substitutes its own
  default when the header is absent — it has to be pinned to `identity`, or the orchestrator stays
  free to compress an SSE stream. The unit test passed while the real server sent `gzip, deflate`.

  This also retires half of a documented constraint. `NEXT_DIST_DIR` existed because a second dev
  server booting off a warm build directory served the first one's baked API URL — one of the two
  ways a dual-backend run could report a green ".NET" pass without touching .NET. A build now
  encodes nothing about the backend. The failure shape survives as a misconfigured
  `ORCHESTRATOR_URL`, so `assertFrontendTalksToOrchUrl` stays.

### Fixed

- **`AGENT_REGISTRY` degraded silently instead of failing.** Filed as "hardcoded host:port"; the
  actual problem was larger. A validating parser already existed on both stacks, was tested, and
  **no production call site used it** — all four sites re-parsed the JSON by hand, and three
  swallowed a malformed value into an empty registry, which builds, serves, passes a health check
  and cannot route. There is now one validator per stack — `shared.factory.parse_agent_registry`
  and `AgentSettingsLoader.ParseAgentRegistry` — both throwing on malformed JSON, a blank URL or a
  scheme-less one, and both asserting the same accepted and rejected inputs, because a stack that
  accepts what the other rejects is a parity gap. Scheme and host are checked; the port is not,
  since a managed endpoint does not have one and requiring it would reject exactly the deployment
  this validates for. Added as the ninth row of
  [`docs/reported-vs-actual.md`](docs/reported-vs-actual.md).

- **Four documentation claims that were false rather than merely stale.** Found while preparing
  to link readers at these exact pages. `NEXT_PUBLIC_BACKEND_STACK` was documented in seven places
  as the way to switch backends, including the README sentence telling people how to run the .NET
  stack — nothing has ever read that variable, and the proxy change removed the last place it was
  set. `docs/database-schema.md` claimed the seeder runs `generate_embeddings.py` as a post-step;
  it does not, so `product_embeddings` is empty after the documented one-command start and
  `semantic_search` is lexical-only until a separate command runs — silent, because full-text
  still carries the result. Both launcher scripts justified the one-stack-at-a-time guard on
  `NEXT_PUBLIC_API_URL` being inlined at build time, which the proxy retired. And 17 of the 27
  `(line N)` citations in `docs/concepts/` pointed at the wrong line — all four in the agent-harness
  page off by 33 — so the numbers are gone, keeping the file links and symbol names a reader
  actually navigates by.

- **Three pre-existing test defects**, each confirmed against a control frontend built to bypass
  the proxy, so none was caused by this work. `chat-generative-ui` and `chat-shopping` asserted
  that exactly one card rendered where the app renders one per result; `chat-shopping`'s
  add-to-cart flow selected product-grid anchors that stopped existing when those cards moved to
  `onClick` + `router.push`, so it burned its full 90-second timeout on an element that cannot
  exist. All three now assert presence, which is the criterion the parity gate already states.

## [1.3.0] - 2026-08-27

The .NET backend could not answer a single question in this window, and the README said the repo
had "two complete, working backends". Every container reported healthy the entire time. That is
the fourth defect in this project found by running the software rather than reading it, and the
first one where the health checks actively argued against looking.

### Fixed

- **The .NET orchestrator could not reach a single specialist.** 39 of 46 .NET tools were
  registered under their C# PascalCase names while the shared prompt corpus — which the .NET
  Dockerfiles copy verbatim out of `agents/python/config/` — names them in Python's snake_case. The
  model was told about one name and offered another on every turn. On the orchestrator's routing
  tool that was fatal (`The arguments dictionary is missing a value for the required parameter
  'agentName'`); on the other 38 it degraded silently, which is worse to find. Tools are now
  registered through one helper that owns the naming rule, and a test asserts no source file
  bypasses it.
- **…and the model does not send consistent argument casing anyway.** With the schema declaring
  `agent_name`, the model still intermittently sent `agentName`, and MAF's binder rejects the call
  outright. The user saw "there was an issue accessing the inventory details" and the stack looked
  healthy. Only a browser run caught it — API spot-checks had hit the lucky casing every time.
  Inbound argument names are now normalised to whatever the schema declares, for every tool rather
  than the one that failed.
- **`handoff` mode took 100–200s and returned 19–25k characters** where `tool` mode answered the
  same prompt in ~11s and ~1,000. The plan's hypothesis — quadratic accumulation of cumulative
  stream text — was measured and refuted: the deltas were genuine. The real cause was the start
  agent. Handoff was seeded with the tool-calling orchestrator, so it routed *and* answered
  instead of handing off; 5,403 updates and 23,637 characters all came from `orchestrator`, with no
  specialist speaking at all. Python now starts from a tool-free triage agent (1,374 chars, ~8s),
  and .NET's hand-rolled router was replaced with a real `AgentWorkflowBuilder` handoff mesh.
- **`workflow:pre-purchase` answered from half its inputs, silently.** Four executors ran and the
  reply was 48 characters. An earlier diagnosis in this repo blamed the synthesis step; that was
  wrong, and is corrected here. The synthesis was faithful — it read `sentiment` and `options`
  where its own tools return `overall_sentiment` and `shipping_options`, so two of the four
  contributions were always absent. The test stubs encoded the same wrong contract, which is why
  the tests passed. Now 127 characters carrying all four.
- **`HANDOFF_MAX_TURNS` was never read from the environment.** It was added to the settings record
  and to the mode, but not to the loader, so setting it did nothing at all. Guarded by a test that
  fails if a setting exists in one place and not the other.
- **.NET tools returned bare `null` on failure.** Correct C#, useless to a model: it learns
  something did not happen but not what or why, so it cannot recover. Observed end to end — the
  agent called `get_order_details` without a UUID, got `null`, and told a customer with eleven
  orders there "may be a temporary issue accessing your order data". Failures now return a
  structured error that names the recovery, matching Python's convention. Genuinely-empty results
  are deliberately not errors.
- **.NET wrote `[DONE]` before persisting the turn.** `[DONE]` is the client's cue that the turn is
  over and the composer re-enables on it, so a follow-up sent immediately could read history before
  the `INSERT` landed and lose the turn it was following up on. Python fixed this as #9; .NET kept
  the old order, so the two stacks disagreed on whether a completed turn was durable.
- **Docker builds compiled under laxer rules than CI.** All seven .NET Dockerfiles copied
  `Directory.Packages.props` but not `Directory.Build.props`, so images silently lost
  `TreatWarningsAsErrors`, `LangVersion` and `InvariantGlobalization` — and shipped unversioned.
- **A stale database volume broke search with no error.** `scripts/dev.sh` probed for stale
  credentials but not a stale *schema*, so a volume predating the full-text-search migration
  started cleanly and then failed at query time.
- **The two stacks collided on ports** with a raw Docker error. Each stack now gets its own Compose
  project name, the second refuses to start with the command to fix it, and `--switch` does the
  swap properly.
- **The evals suite went red on a day nobody changed anything.** `get_sentiment_trend` buckets
  reviews by calendar month over a `NOW()`-relative window, and the seeder places each review at a
  fixed day-offset from seed time. The set of reviews in the window is invariant; which calendar
  month a fixed offset lands in is not, so the same 15 reviews made 7 buckets on the recording date
  and 5 later. The replay hash already scrubbed the month *labels* and stopped there, so the
  fixture key had quietly become a function of the wall-clock date. Re-keyed offline — no API spend.
- **Three E2E tests asserted against a mocked API** and one asserted a nav link whose removal was
  deliberate. Repaired, and what they were hiding is recorded rather than quietly fixed.

### Added

- **Complete .NET tutorial coverage** ([#70](https://github.com/nitin27may/e-commerce-agents/pull/70)) —
  every chapter that ships code now ships both languages, both gated in CI: 334 .NET tests across
  31 projects, up from 47 across 11. Turning the gate on found chapter 12's sample had never run
  (bare string input, no `TurnToken`, and a match on an event type that is never emitted) and
  chapter 15 looping forever because a termination check never chained to its base.
- **An approval control inside the chat thread.** The pause-and-resume loop was already real on
  both stacks, but the only control that could release a pause lived on `/runs` — so the user who
  caused it, looking at a message that stopped mid-return, had no way to act without knowing a
  separate page existed. The blocker was that no streaming client ever learned the run's id; both
  stacks now emit `event: run` after persistence and before `[DONE]`.
- **Tool steps that arrive before the answer does.** Both stacks batched their timeline steps until
  after the last text chunk, so the timeline appeared once the answer had finished writing —
  exactly when it stops being useful. Both specialist hosts now drain steps before each chunk and
  both orchestrators forward them live.
- **A cost counter this repo owns, on both stacks.** Python's `get_meter()` had been exposed since
  telemetry was wired up and never called, and .NET emitted no custom instrument either, so the one
  number this application knows — what a run costs — existed only as a log line, which cannot be
  alerted on. Now `ecommerce.llm.cost.usd` with tokens split by direction beside it, under the same
  meter name on both backends so one dashboard covers them, and nothing user-scoped in the
  attributes.
- **A composer that responds to the conversation** ([#4](https://github.com/nitin27may/e-commerce-agents/issues/4)) —
  six always-visible mode chips collapse into one picker, and the suggestion row is derived from the
  assistant's last message (its typed card payload first, its closing question second) rather than
  being the same four canned prompts after every turn. No LLM call.
- **Published orchestration-mode benchmark** ([`docs/orchestration-benchmark.md`](docs/orchestration-benchmark.md)) —
  latency, tokens, cost and response length per mode, with the prompt set, date, model and commit,
  because a benchmark without its conditions is an anecdote.
- **Five architecture decision records** ([`docs/adr/`](docs/adr/)) — A2A over direct calls, no
  text-to-SQL, YAML prompt composition, MAF-native execution, dual-stack parity. Each states what
  would make it wrong.
- **[`docs/reported-vs-actual.md`](docs/reported-vs-actual.md)** — eight cases where the reported
  problem turned out smaller than the actual one, every time, and each found by running rather than
  reading. It was the most credible artifact in the repo and it was invisible in `.claude/`.

### Changed

- **The `full` eval job no longer runs on a schedule.** It spends a real API key, and a weekly cron
  bills on a timer for a result nobody asked for — the 2026-08-24 scheduled run failed and sat
  there unread. It is `workflow_dispatch` only now, matching the .NET eval job, and the rule is
  written into the workflow so the next job added there inherits it.
- **ESLint no longer lints `.next-dotnet/`.** One `dev.sh --dotnet` run put 455 generated files in
  scope and `pnpm lint` reported 2,072 errors no source change could fix. A gate that only fails is
  one people learn to skip.


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
