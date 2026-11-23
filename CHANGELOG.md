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

### Fixed

- **Azure first-run hardening (optional deployment only).** The Postgres SKU preflight read the wrong level of the
  `az postgres flexible-server list-skus` JSON (SKUs are nested under `supportedServerEditions`), so it rejected
  every region and `scripts/azure-up.sh` could not get past `prereqs`. Fixed against a live read-only listing and
  pinned by `tests/test_azure_checks.py`. Also: resource providers are registered in parallel and polled (a new
  subscription needs minutes), `azd auth login` and the subscription are checked and pinned, the embedding model
  quota is checked as well as the chat model, `Microsoft.Network` and (for the gateway) `Microsoft.ApiManagement`
  are registered, purge-protected and API Management soft-deleted names are recognised, `azure-down` survives a
  failing `azd down`, the smoke test waits for revisions to become healthy, and `azd provision` replaces `azd up`
  (the template declares no services). `docs/azure-deployment.md` now opens with a first-deploy checklist. Nothing
  outside `infra/`, `scripts/azure*` and the docs changed; the dev, demo and eval paths do not need Azure.

- **Group chat no longer answers with a placeholder.** The moderator's default verdict was the one line
  "Synthesized 2 perspective(s) (value, quality) on: <prompt>"; it now shows the question followed by each
  panelist's actual words (#210). No model call is involved, so no replay fixture changes.
- **Chat: a slow conversation load no longer overwrites a newer choice.** Opening a conversation and then clicking
  "New chat" before its messages arrived could bring the old conversation's messages back (#210).

### Added

- **Live-capable eval datasets.** `evals/datasets/live/*.live.json` (order management, inventory, orchestrator
  routing, review sentiment, pricing and promotions, product discovery) name orders and products as `{{order:1}}`,
  `{{order:shipped}}` and `{{product_id:NAME}}`, resolved against the run's database (`evals/live_facts.py`) and as a seeded customer, so a
  live model is no longer asked about order ids that do not exist (order management scored 49.2% live for that
  reason). The records store the resolved text. Replay datasets and fixtures are unchanged and pinned placeholder-free.
- **Run profiles.** Twelve provider setups (`keyless`, `openai`, `azure-key`, `azure-identity`, `anthropic`,
  `gemini`, `ollama`, `openrouter`, `llama-cpp`, `openai-compatible`, `azure-gateway`, `foundry`) in
  `agents/python/config/profiles/`. Each sets chat, embeddings and the model together, holds no secret, and states
  its tier, key, cost, .NET support and how it was verified (most were stub-tested or not tested at all, and the
  header says so). `./scripts/dev.sh --profile NAME` (`dev.ps1 -Profile`, `replay-evals.sh --profile keyless`,
  `evals.run_evals --profile`) applies one: your shell wins, then the profile, then `.env`. An unknown name is
  refused with the valid list. `docs/run-profiles.md` has the table.
- **`python -m scripts.doctor`.** Prints the effective configuration with secrets redacted, validates it, and checks
  the model endpoint, database, Redis and every registry entry without spending tokens. `--json`, exit 1 on a hard
  failure. The capability check is a marked stub until the eval work adds it.
- **One structured startup line per service on both stacks** (provider, model, embedding model, auth mode, pricing
  source, budget mode), with no secret. Parity matrix row 51.
- **Gateway attribution headers on every model call, both stacks (AZ-4).** Chat and embeddings requests
  carry `x-agent-name`, `x-run-id` and `x-eval-run-id`, so an AI gateway can count tokens per agent and per
  run. Values are sanitised and capped, an empty one is omitted, and no user identity or secret is sent.
  `GATEWAY_HEADERS_ENABLED` (default true) turns it off; `EVAL_RUN_ID` sets the eval run id. Replay keys and
  every fixture are unaffected (`fixture_drift` exit 0). Parity row 52.
- **Foundry evaluators adapter (EV-8), optional.** `uv sync --extra foundry-evals` adds `azure-ai-evaluation`;
  `python -m evals.foundry.run --records <file>` maps run records onto Foundry's groundedness, intent resolution,
  tool-call accuracy, task adherence and indirect-attack evaluators, runs them locally against a judge deployment
  you name (`FOUNDRY_EVAL_*`), writes results next to the records, and optionally logs the run to a Foundry project
  (`AZURE_AI_PROJECT`, `--log-to-project`). The core eval path imports no Azure package and never imports the
  adapter; a test enforces it.
- **Community eval results (EV-14).** Anyone can contribute a result file for their own provider and model under
  `agents/python/evals/community/` (format, how to run, how to submit by PR in its README).
  `python -m evals.community_validate <file>` checks the schema, that the setup is complete, that the scores follow
  from attached records (re-scored with no model call), and refuses secret-looking strings, absurd scores, an
  undisclosed judge and an unlabelled self-judged run. The eval scorecard lists accepted files in a separate table,
  "Community-submitted, not verified by the maintainers", never in the reference rows.
- **`COST_BUDGET_TOKENS_PER_RUN`, a per-run token ceiling on both stacks.** It sits beside the dollar
  ceiling, under the same `COST_BUDGET_MODE`, and unlike it still holds for a model with no price on file.
  In `enforce` the next LLM turn is refused once a run has used that many input plus output tokens, and a
  turn that reports no usage also stops the run (fail closed). Unset, empty or 0 means off.
- **`usage_logs.price_source` and `priced_at`** (migration `0015`, both stacks). Each costed row now says
  which pricing file and version priced it (a short content digest, or `override:<file>`) and when, so a
  later price change can be told apart from the price a row was computed with. Older rows stay NULL.
- **`python -m evals.run_evals --estimate`** prints the token count and cost a live run would have, from the
  replay fixtures and the price file, and exits without calling a model. **`--concurrency`** bounds live
  eval calls and a 429 or 5xx is retried with a `Retry-After`-aware, jittered, capped backoff.
- **Optional Azure modules for Foundry, an AI gateway and per-app identities (Bicep, offline only).**
  `deployFoundryProject` adds a Foundry project under the AIServices account with the App Insights
  connection and Azure AI User for the app identity. `deployGateway` adds an API Management instance in
  front of the account with per-agent token limits and token metrics keyed on `x-agent-name`, managed
  identity to the model and the endpoint handed to the apps through the existing `AZURE_OPENAI_ENDPOINT`.
  `deployPerAppIdentities` adds one user-assigned identity per app (attached to the orchestrator, the
  specialists and the outbox worker). All three are off by default. The `azure` provider pointed at the account is documented as
  Foundry for OpenAI models; there is no `LLM_PROVIDER=foundry`. None has been deployed: the APIM tier
  that supports the token policies is unverified (see `docs/azure-deployment.md`).
- **The `reference` Azure profile is real.** A VNet with a delegated Container Apps subnet and a private endpoint subnet; private endpoints and private DNS zones for PostgreSQL, Key Vault and the AIServices account, all with public access off; Azure Managed Redis in place of the redis container; a Basic Container Registry with `AcrPull` for the app identity; Entra auth to the model by default; `A2A_IDENTITY=token` with the distinct generated key. The demo profile deploys exactly what it did (a stored resource list pins it). Written and compiled offline, never deployed.
- **Build from source on Azure.** `scripts/azure-up.sh --build-from-source` builds every image in ACR Tasks (`az acr build`) from the working tree; `--image-source acr` imports the published images instead. `--dry-run` prints every command.
- **.NET on Azure.** `stack=dotnet` (`--stack dotnet`) deploys the .NET orchestrator, specialists and inventory MCP host; `build-images.yml` now publishes seven `<service>-dotnet` images (amd64) with a `/health` smoke test that has not yet run in CI. Offline-verified only; parity row 54.
- **The eval `http` target (EV-2).** `python -m evals.run_evals --target http --base-url <orchestrator> --stack <name>`
  logs in as a seeded user, drives `POST /api/chat/stream` and writes the same run records as the in-process target, so
  the same datasets measure the Python stack, the .NET stack, a mixed stack or a deployed URL with no database or model
  key in the eval process. Credentials come from `EVAL_USER_EMAIL` / `EVAL_USER_PASSWORD` (seeded demo defaults). Facts the
  wire cannot show are listed per record in the new `not_observable` field and assertions on them are skipped, not failed.
  Cases may set a `mode` (`workflow:pre-purchase`); records gain `nodes`, and the setup gains the endpoint host.
- **A stack-parity gate in CI (B2).** `.github/workflows/stack-parity.yml` boots the Python and .NET stacks under replay,
  runs `evals/datasets/stack_parity.json` through the http target on each and fails when they diverge on tool calls,
  routes, workflow nodes, approval flags or mean score (`python -m evals.stack_compare`). Keyless. It covers turns that
  call no model; a model-calling comparison still needs a key. `scripts/eval-http-parity.sh` checks the http target
  against the in-process one on a running stack.

### Changed

- **Aspire AppHost moved to 17.0.0** (Hosting.PostgreSQL, Redis, Python, JavaScript, Testing, plus the new
  Hosting.AppHost and Hosting.Dotnet). Aspire 17 requires .NET 10 and obsoletes `Aspire.AppHost.Sdk`
  (ASPIRE012, an error under warnings-as-errors) and `AddProject<T>`. The AppHost now uses `Microsoft.NET.Sdk` and
  `AddDotnetProject` by project path; the tests assert `DotnetProjectResource`. Supersedes Dependabot PRs 231,
  232, 233 and 235. The Anthropic/Dapper/Npgsql bumps of PR 237 were already on main.

- **The pricing directory is mounted into every agent container** (`/pricing`, read-only, all three compose files),
  so a price edit or `PRICING_OVERRIDES_FILE` takes effect on restart with no rebuild. `host.docker.internal` is
  mapped to the host so local model servers work from containers on Linux.
- `dev.sh` / `dev.ps1` key preflight understands `AZURE_OPENAI_AUTH=identity` (no key needed) and
  `LLM_PROVIDER=anthropic`. `scripts/reset_embeddings.py` gains `--if-needed`.
- **No model price is written in code any more.** Both stacks read one file,
  `agents/python/config/pricing/models.yaml`, in USD per 1M tokens, with a source, a date and a
  `verified` flag on every row. Anyone can correct a price or add a model there; `PRICING_OVERRIDES_FILE`
  adds private entries without a repo change. Shared golden cases keep the two stacks pricing identically.
- **An unknown model's cost is now unknown, not gpt-4.1's.** Both stacks used to price any unlisted model
  (an Ollama model, a Gemini model, a custom Azure deployment name) as gpt-4.1, so cost columns and eval
  reports showed invented dollars. They now show null. With `COST_BUDGET_MODE=enforce` and a ceiling, a
  turn on an unpriced model stops the run instead of letting the ceiling pass unchecked.

### Fixed

- **Foundry evaluator inputs now use the shapes the SDK documents.** The adapter passed `query` and `response` as
  plain strings; `IntentResolutionEvaluator` and `TaskAdherenceEvaluator` then logged "Conversation history could
  not be parsed" and "Agent response could not be parsed" and judged an unstructured prompt, which degrades scores
  without failing the call. They now get message lists (a user message; the tool calls, then the final answer as
  assistant messages), `ToolCallAccuracyEvaluator` gets message-list queries and `{name, description, parameters}`
  definitions, and a record that calls a tool with no definition is skipped by name. New
  `--tool-definitions auto` reads the definitions off the agents that produced the records, so records with tool
  calls are scoreable without a hand-written file. Tests check the shapes against the installed SDK's own
  validators and reformatters, offline. Live check on 5 pricing records (gpt-4.1-mini judge): both warnings gone,
  intent resolution unchanged at 4.2, tool call accuracy now scores 4 records (mean 4.25).
- **Order cards built from the recent-orders summary.** The context provider on both stacks lists each recent order as
  id, status, total and date only, and the order-management agent could render an `order` card straight from that line,
  so the card shipped with empty `items`, address, carrier and tracking and no tool was called. The injected block now
  says it is a summary and points to `get_user_orders` / `get_order_details` for any card. No replay fixture contains
  the block, so none is re-keyed (the prompt YAML is deliberately untouched: 15 fixtures embed it).
- **The eval harness could not see a parked approval-gated tool call.** `HITLFunctionMiddleware` writes
  `tool_approval_requests` and short-circuits before the step recorder, but the harness counted only
  `hitl_requests` (workflow pauses), so an `expect_hitl` case on a tool-mode `cancel_order` could never pass and the
  live order management cancel case failed with an empty response. A pending `tool_approval_requests` row for the
  case user created during the run now counts as an approval pause, in-process and, when the eval process can read
  the served database, over `--target http` (where it also clears `hitl` from `not_observable` and no longer turns a
  parked turn into an "empty response" error). `expect_hitl: true` is set on the live cancel case. Replay results are
  unchanged.
- **Grounding ledger records coupon amounts.** `GroundingLedger._record_bare_amount` skipped `min_spend`,
  `max_discount` and `discount_value`, so an honest answer about active deals scored groundedness 0 (the
  pricing-promotions specialist, 0 of 10 amounts, in the 2026-11-23 live run). Those fields are recorded now; an amount
  absent from every tool result is still unverifiable. Python only: the .NET verifier has no ledger. Committed replay
  baselines are not re-recorded.
- **The Mode dropdown shows its labels in full.** The list is as wide as its longest label; it used to take the
  trigger's width and cut three of the five off (#210).
- **The Compare dialog renders answers the way chat does.** A fenced card block in a mode's answer (a product, a
  sentiment summary) shows as its card. It used to be printed as JSON text (#209).
- **Orchestrator eval groundedness no longer scores relayed amounts as ungrounded.** In the live 2026-11-23 run the
  orchestrator suite scored 0.0 on "speaker under $100" (the user's own budget echoed) and "coupons" (real coupon
  thresholds relayed from the specialist): the orchestrator has no tools of its own, so its ledger is empty and
  amount/tracking claims are unverifiable by construction. `score_from_report(..., ledger_complete=False)` leaves
  those verdicts out for the `orchestrator` target; ids and cards still count and specialist suites are unchanged.
  Production grounding and replay fixtures are untouched (`docs/agent-quality.md`). Gap left open: the ledger does
  not record `min_spend` / `max_discount` / `discount_value`, so the pricing specialist scores its own coupon list
  0 of 10.
- **Staff could not decide a paused return from the Approvals page, and the support role had no
  page to decide from** (#208). The page read the older tool-approval list, so a return paused at
  the workflow's policy gate never appeared and the inbox said "No pending approvals"; it was also
  admin-only, while support's Runs page lists only its own runs. The page now reads the unified
  inbox (`GET /api/approvals`) and decides through `POST /api/approvals/{id}/decide`, and it is
  open to `support` and `admin`. The server still decides what each reviewer sees and may decide.
  Web only; the API was already correct on both stacks.

### Documentation

- **Screenshots of the orchestration UI** (the Compare dialog, a return paused for approval, an admin
  approving from Runs) in the README and the demo guide. The demo guide's approval scenario is
  corrected after running it: it needs the demo-tour account, and an admin decides from Runs.
- **The roadmap is reorganised for a visitor:** what is next, what each release shipped, what is
  not planned. It said there was no Azure path and that a third model provider was still to come;
  both were out of date.
- **The parity matrix has a summary by status** (27 of 48 rows at full parity) ahead of the table,
  and its rows state the current position first. One row had a stray cell that left its status
  empty.
- **Two new pages:** `docs/checkpoints-and-resume.md` (how a workflow run pauses for approval and
  resumes in a later request) and `docs/replay-mode.md` (how the project runs with no API key, and
  what a replay miss is).
- **Guides say what is true now,** without plan codes or history. Corrected along the way: the
  approvals page said merchant limits were unused (they are enforced), the telemetry page said
  export was OTLP over HTTP only (gRPC is tried first), the frontend page said Playwright never
  runs in CI (a UI smoke subset does), and the security guide's list of unauthenticated paths
  lacked `/ready`.
- `CONTRIBUTING.md` gains prerequisites, a keyless test section and a PR checklist.
- **Quick start leads with the shortest path** and no longer tells Windows users to drive Docker
  Compose by hand; `scripts/dev.ps1` takes the same flags.
- **Concept pages each end with the chapter that builds the idea and where it runs in the
  application.** The production-concerns page said the application had no idempotency, retry or
  rate limiting; it has all three, and the page now describes them. The orchestration page had the
  return workflow's gate after the return instead of before it.
- **API reference documents the orchestration endpoints** (modes, graph, compare, resume) with
  their shapes, and no longer says `POST /api/chat` requires a token (anonymous callers reach
  product discovery only).
- **Corrected against the code:** the audit matrix said .NET has no stored-injection defense; the
  benchmark said only `tool` mode records usage; releasing said eleven images and four gating jobs
  (twelve and six); the database schema page omitted migration 0014 and two seeded users.

## [1.4.0] - 2026-11-21

Three months of work on `main` reach a release: A2A 1.0 on both stacks, one approvals inbox routed
by a policy file, an Anthropic provider, a no-key demo mode, and a first Azure deployment path.
It also fixes an unauthenticated route that could show another customer's return label, which is
the reason to upgrade even if you want none of the rest.

Several things in this release are written and tested but have not run against the real service
they target. They are listed under "Known limits" so nobody discovers them the hard way.

### Known limits

- **The Azure deployment has never run against a real subscription.** The Bicep, the azd hooks and
  the scripts build, lint and pass offline tests. The first real deploy is still to come. The GHCR
  packages must be public for that path, and `db-init` is a twelfth image.
- **Azure managed identity (`AZURE_OPENAI_AUTH=identity`) was tested against a fake credential
  only.** The default stays `key`.
- **`Azure.AI.OpenAI` is on a prerelease, 2.9.0-beta.1,** on the .NET stack. No stable version is
  compatible with the OpenAI package the agent framework pulls in.
- **The Anthropic provider is covered by stubbed-transport tests on both stacks and has never made
  a live call.**
- **The .NET Aspire AppHost has not been booted.**
- **The eval scorecard is replay-only.** No live-model eval run has been recorded for this release.
- **`--no-key` starts the stack, but the recorded chat answers are not in the released images
  yet,** so chat prompts report "not recorded". Browsing, search, cart and checkout work. The
  tutorials are the fully keyless path.
- **An unattended .NET return still cannot auto-expire** (parity matrix row 36). The Python worker
  cannot expire it and the .NET timeout worker is not in this release.

### Documentation

- **The README, the docs landing page and the repository description now lead with what this is:**
  a complete multi-agent application with a course that builds up to it. The README gains a
  "What is in the box" table and the five orchestration modes with the benchmark's latency figures.
  Before this, neither the README nor the description mentioned that the orchestrator runs one
  request five ways.
- **Orchestration modes are documented where a reader looks for them:** a section and diagram in
  `docs/architecture.md`, three scenarios in `docs/demo-guide.md` (switch mode, compare modes,
  approve a paused return), and a chapter-to-mode map in the tutorial index.
- **Pages that said an API key was mandatory no longer do,** `scripts/verify-setup.sh` accepts
  `LLM_PROVIDER=replay`, and twelve chapters dropped a key prerequisite their own code did not need.
- **All 34 tutorial chapters were edited for learners.** Each opens with what you will be able to
  do, shows the real output of its first keyless run, and names the keyless .NET path. Four
  chapters documented a run as keyless that fails without a key (03, 06, 15, 25); chapter 12 listed
  the return workflow's steps in the wrong order; chapter 14 gave the wrong .NET handoff tool
  names. Stale file and line references into the application are corrected.

### Security

- **Return label route matched tokens with `LIKE '%<segment>%'`** (both stacks). `GET /api/returns/{label_token}/label`
  is unauthenticated, and a path segment of `%`, `_` or a partial token matched another customer's return and rendered
  their name, email and address. The route now accepts only a full 12-character lowercase hex token, looks the return
  up by exact equality on the stored URL, and answers one 404 for unknown and malformed tokens alike.

### Added since 1.3.0

- **Notebooks for tutorial chapters 01 to 04** (C-20, #100). `tutorials/0N-*/python/notebook.ipynb` walks each chapter
  one cell at a time, importing the chapter's own `main.py` and reusing the exact prompts its replay fixtures were
  recorded with, so they run with no key. `tutorials/tests/test_notebooks.py` executes all four headlessly with
  `nbclient` (also a step in the `tutorials-keyless` job) and fails if a committed notebook carries outputs;
  `scripts/strip_notebook_outputs.py` cleans one. `nbclient`, `nbformat` and `ipykernel` join the tutorials `dev`
  extra; the lock change is additions only.
- **Managed identity for Azure OpenAI** (#89, P3; both stacks). `AZURE_OPENAI_AUTH=identity` (default `key`) sends
  a Microsoft Entra token for chat and Azure embeddings instead of `AZURE_OPENAI_KEY`, which is then not required;
  `AZURE_CLIENT_ID` selects a user-assigned identity, else `DefaultAzureCredential`. Adds `azure-identity` on Python
  (no existing dependency moves). Tested with a fake credential only; the default is not flipped. Parity row 49.
- **Runtime knobs for managed hosting** (#89, P2; both stacks). `LLM_TEMPERATURE` set empty sends no
  temperature (gpt-5.x rejects it; default `0.2` unchanged, replay keys unaffected).
  `DB_POOL_MIN_SIZE` / `DB_POOL_MAX_SIZE` bound the connection pool (Python defaults `5`/`20` as before;
  .NET leaves Npgsql's defaults unless set). The .NET stack now honours `sslmode` in `DATABASE_URL`, which it
  used to drop. Estimated `gpt-5.1` and `gpt-5.1-mini` price rows on both stacks. Parity row 48.
- **History compaction on .NET** (B-15, parity row 43). `HISTORY_COMPACTION_KEEP_LAST_GROUPS` (default `0`, off) now
  also trims the .NET specialist host's rehydrated history and the orchestrator's conversation history to the last N
  message groups, a group being a user turn with its assistant and tool messages, kept or dropped whole. Off, the
  history and so the model request are unchanged.
- **NET-36: a paused .NET return now times out on its own** (parity row 36 is Full). The .NET orchestrator runs a hosted service
  (`ApprovalTimeoutWorker`, `DOTNET_TIMEOUT_WORKER_ENABLED`, default on) that drains `approval.timeout_apply` for approvals a .NET
  workflow opened, resuming the run as a rejection as the requester under the same claim fence as a staff decision. The two
  stacks share the topic, so `ApprovalIntake` tags those approvals `recommendation.stack = "dotnet"` (no migration) and the
  Python worker's claim skips them. Approvals opened before the tag still go to Python and are parked as before.
- **A2A 1.0 on both stacks** (#88). Every specialist serves the A2A 1.0 JSON-RPC binding at
  `/a2a/v1` and an agent card at `/.well-known/agent-card.json`, beside the unchanged
  `/message:send` wire. `A2A_TRANSPORT=legacy|a2a` (default `legacy`) picks what the caller
  speaks, and `A2A_IDENTITY` lets the user's identity travel as a signed, short-lived
  `X-User-Context` token. Python uses the official `a2a-sdk`, .NET the official A2A SDK
  (a preview, pinned exactly). Interop is tested in both directions by `scripts/a2a-interop.sh`.
  See `docs/a2a.md`.
- **Approvals** (#92). One inbox and one decide path on both stacks, routed by
  `config/policies/approvals.yaml`, with an append-only audit trail, claim fencing against
  double execution, and an outbox worker that sends reminders, escalates on an SLA breach and
  applies the timeout. See `docs/approvals.md`.
- **Merchant proposals** (#93, Python). Deterministic seller markdown and restock suggestions,
  decided line by line through the approvals machinery and applied at most once. See
  `docs/merchant-proposals.md`.
- **Event autonomy** (#94, Python). Budgets for what the system may do on its own when an event
  arrives, with an append-only decision log and ledger. An action over budget becomes an
  approval. Off unless `AUTONOMY_ENABLED`. See `docs/event-autonomy.md`.
- **Return lifecycle** (#95, Python). An approved return waits for the carrier's first scan across
  restarts, then for support's inspection, and pays out once. Off unless
  `RETURN_LIFECYCLE_ENABLED`. See `docs/return-lifecycle.md`.
- **Anthropic provider and separate embeddings configuration** (#96). `LLM_PROVIDER=anthropic` on
  both stacks (tested against a stubbed transport; never run live), and `EMBEDDING_*` settings
  independent of the chat provider.
- **MAF DevUI entry point** (#98). `scripts/devui.py` browses the specialists and workflows
  without changing the lock file. See `docs/devui.md`.
- **`dev.sh --no-key` / `dev.ps1 -NoKey`** run the stack on recorded model responses, with no API key.
  `docker-compose.nokey.yml` is an overlay for `docker-compose.demo.yml` (or the from-source
  `docker-compose.yml`) that hardcodes `LLM_PROVIDER=replay`, `REPLAY_FIXTURES_DIR`,
  `DEMO_TOUR_ENABLED` and the prompt- and schema-shaping settings (MCP, handoff mode, guardrails,
  `AGENT_REGISTRY`). They are literals rather than `${VAR:-}` so an exported variable cannot change
  what a recorded answer was keyed on. `tutorials/21-capstone-tour/run.sh` wraps `--demo --no-key`.
  The recorded demo fixtures are not in the images yet (the recording session adds them); until then
  every model call is a reported replay miss and the stack stays up. The key preflight now points at
  `--no-key`, and `--demo --clean` also clears the demo stack's volumes.
- **Aspire AppHost** (B-18, #90, .NET). `agents/dotnet/src/ECommerceAgents.AppHost` runs the stack under
  Aspire as an alternative to compose, which stays the reference path: pgvector Postgres, Redis, the repo's
  own `scripts/migrate.py` and seeder as one-shot containers, the auth-server, the orchestrator and
  five specialists, the outbox worker and the pnpm frontend, with `AGENT_REGISTRY` built from endpoint
  references. `AgentStack=python` swaps the orchestrator and specialists to uvicorn apps, and
  `AgentStackOverrides:<name>=python` moves one. `ECommerceAgents.ServiceDefaults` reuses `TelemetrySetup`.
  `AppHostModelTests` build the model without starting it. Pinned to Aspire 13.6.5 (later moved to 17.0.0; see Unreleased); 17.0.0 obsoletes
  the SDK and the project-resource model. See `docs/deployment.md`.

### Changed since 1.3.0

- The Compose Redis image is `redis:8-alpine` (CI still runs `redis:7`).
- The MCP servers moved to `mcp` 2.x with the MAF upgrade.

### Removed

- **`NEXT_DIST_DIR`** in `web/next.config.ts`. A build encodes
  nothing about the backend since the frontend proxies `/api/*` to `ORCHESTRATOR_URL` at runtime,
  so the reason for a separate build directory is gone. It also rewrote the committed
  `web/tsconfig.json` on every build that used it.

### Azure pre-work

Azure pre-work. Two application-code blockers that would each have stopped a deployment, neither of
them about Azure services. Both are contract changes any additional backend inherits, which is why
they landed before the infrastructure work rather than during it.

### Added

- **`db-init`, a one-shot database bootstrap image for Azure.** `scripts/` and `docker/postgres/` were
  only bind-mounted by compose, and the agent image's build context is `agents/python`, so a managed
  environment had no way to create the schema. `agents/python/Dockerfile.dbinit` (repo-root context,
  non-root) runs `python -m scripts.db_init`: migrate, seed only if `users` is empty, embeddings only if
  the table is empty and a real provider is configured, `ANALYZE`. It is the twelfth image in
  `build-images.yml`, with a smoke import.
- **`seed --if-empty` / `SEED_MODE=if-empty`** skips a database that already has users instead of
  clearing it, and **`SEED_STAFF_PASSWORD`** replaces the published admin, support and seller demo
  passwords. Neither draws from `random` or changes any other seeded row.

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

- **.NET Azure OpenAI chat threw `MissingMethodException` at request time.** `Azure.AI.OpenAI` 2.1.0 was pinned
  next to the much newer `OpenAI` 2.14.0 that `Microsoft.Agents.AI.OpenAI` brings in, and
  `AzureChatClient.PostfixSwapMaxTokens` called `ChatCompletionOptions.get_SerializedAdditionalRawData`, which no
  longer exists. Any call through the SDK's native `ChatClient` that set a max token count failed before the request
  was sent, in key mode as well. `Azure.AI.OpenAI` is now `2.9.0-beta.1` (no stable release is newer than 2.1.0).
  `ChatClientFactory.CreateChatClient` takes an optional `HttpClient` so `AzureOpenAiChatClientTests` can drive the
  real SDK against a stub handler. Not exercised: a live call to Azure.
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
