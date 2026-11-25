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

Planned as 1.5.0. Since 1.4.0 the project gains a customer confirmation step before gated actions, more event
autonomy, an opt-in merchant-ops specialist, a key-free hosted preview, a much larger eval harness with live baselines,
and twelve run profiles. Migrations 0015 to 0020 are new (see "Database"). Everything that has not run against its real
target is listed under "Known limits".

### Added

**Approvals and agents**

- **Customer confirmation before a gated call parks (#302, #307; both stacks, parity row 56).** In `tool` mode the host asks the customer to confirm the exact action in plain words before `cancel_order`, `modify_order`, `initiate_return`, `process_refund` or `place_backorder` parks for staff; only an explicit yes on a later turn lets it park. It is a deterministic record, not a prompt: table `customer_confirmations` (migration 0016; one open row per user, session, tool and normalised arguments), the user's words read from the stored `messages` row (never the text the orchestrator's model wrote), a conservative `is_affirmative`, a TTL (`CUSTOMER_CONFIRMATION_TTL_SECONDS`, 600) and `CUSTOMER_CONFIRMATION_ENABLED` (default true). Declines, silence, a stale yes, different arguments and an injected "already confirmed" leave nothing for staff. On .NET the gate is `HitlGate.TryGateAsync` with `CustomerConfirmation` and `CustomerConfirmationRepository` over the same table (no second migration); `CustomerConfirmationGateTests` drives the real gate on a Postgres testcontainer. Sentences and matching vectors are pinned in `config/policies/testdata/customer-confirmation.v1.json`, which both stacks run. No prompt or tool schema changed, so no existing fixture moved; the six live cases that expected a one-turn park are now two turns. A mixed stack no longer skips the question on a .NET specialist. Known difference: under .NET `InvariantGlobalization` only fullwidth compatibility characters are folded (anything else fails closed). New eval datasets: `evals/datasets/approvals.json` (replay, 5 cases, baseline `python-inproc-replay/approvals.json`, a sixth suite in `scripts/replay-evals.sh`) and `evals/datasets/live/approvals.live.json` (10 multi-turn cases; gpt-4.1 under test, gpt-5.4-mini judge, k=1: 10 of 10 passed, overall 96.7%, completeness 83.5%). See [docs/approvals.md](docs/approvals.md#customer-confirmation-before-a-call-parks).
- **PO-value approval threshold for merchant restock proposals (#298; Python, parity row 39).** A restock batch whose PO value (quantity x unit cost over the lines the seller accepts) is over the threshold in `approvals.yaml` (`merchant_proposal`, fact `amount`, placeholder 5000; golden cases in `testdata/merchant_proposal.yaml`, which the .NET policy tests also run) no longer finishes on the seller's word. The accepted restock lines are recorded as `awaiting_manager` and the same `approvals` row is routed to `manager` with a fresh SLA clock; an admin then approves or rejects the lines (lowering a quantity is allowed, raising never). Under the threshold nothing changes, markdown lines never wait, the seller cannot decide the manager tier, whoever decided the seller's side cannot also be the manager, and an SLA breach never expires the batch under a decide in flight. Migration 0019 adds the two statuses and narrows the beneficiary CHECK to the seller's own stage. .NET is unchanged: it answers 404 for source `proposal`. See [docs/merchant-proposals.md](docs/merchant-proposals.md#po-value-threshold).
- **Merchant-ops specialist (#298; Python only, parity row 57).** An opt-in sixth specialist, `merchant_ops/` on port 8086 behind its own compose profile (`MERCHANT_OPS_AGENT_ENABLED=true ./scripts/dev.sh`), explains and orders a seller's pending markdown and restock proposals (the stored rationale and facts, a deterministic approve-first order, why a line is blocked) and triages reviews of the seller's products (abusive, instruction-like, off-topic, low-signal) with a polite draft reply, including text the seller pastes. Seven read-only tools, seller or admin only, identity from the ContextVars; there is no write tool, so every action stays a proposal line the seller decides through the approvals machinery. The orchestrator routes to it only with `MERCHANT_OPS_AGENT_ENABLED` (default off): off, the registry drops it and the orchestrator prompt and routing-tool description are unchanged, so no orchestrator replay fixture moved. Replay dataset `merchant_ops.json` (5 cases, baseline `python-inproc-replay`) and live twin `live/merchant_ops.live.json` (19 cases; one sitting, 16 passed, not stored as a baseline, see `docs/agent-quality.md`). `--agent merchant-ops` is a harness choice (`evals/merchant_setup.py` generates the demo sellers' proposal batches first). See [docs/merchant-proposals.md](docs/merchant-proposals.md#the-merchant-ops-agent).
- **Event autonomy grows to five events, monthly budgets and an optional responder (#299; Python only, parity row 40).** Beside the delay credit from 1.4.0: `product.price_dropped` pays a store-credit price adjustment per recent order line (one per line ever); `inventory.back_in_stock` notifies customers whose cart holds an in-stock product (no money, a per-customer daily cap, a bounded audience); `order.item_damaged` gives a damaged order line one remedy ever, a store credit (20 percent up to 15.00) or a free replacement offer; `order.risk_flagged` scores a placed order from facts already in the database (total, new account, shipping versus billing address, items, orders by the same customer in 24 hours; signals and thresholds in `autonomy.yaml`, no model): low is `no_action`, medium opens an `autonomous_action` approval, high also **holds** the order (`orders.on_hold`, reversible by an admin or by staff clearing the review, recorded as a decision row and a `reversal`). A risk review is never an autonomous tier (the loader refuses one) and the engine never cancels, refunds or changes an order's status. A budget may carry `global_per_month`, checked in the same transaction and by the same helper as the day cap (`lock_budget` takes the day lock, then a month lock); the decisions page shows month use. `AUTONOMY_RESPONDER_ENABLED` (off) has a model draft the customer message after the decision commits; the draft is checked against the decision's facts, falls back to the template, and can never change an amount or a tier. Simulator scenarios `price-drop`, `back-in-stock`, `item-damaged` and `order-risk`, worker handlers, golden cases and `scripts/flag-smoke.sh` cover them (migrations 0017 and 0018). Every cap, signal and threshold is a placeholder.
- **Coupons are single-use per user on both stacks.** `validate_coupon`, `apply_coupon_to_cart`, `POST /api/cart/coupon` and `POST /api/checkout` refuse a code the caller already has a non-cancelled order with (`coupon_already_used`, "You have already used this coupon"); checkout takes a per-user row lock so two simultaneous checkouts place one order. No migration, no seed change.
- **Grounding verifies ids a tool returned this turn, and records more figures.** The bare-id check only knew products and orders; the ledger now also records review, proposal and proposal-line ids and the merchant-ops amounts (`proposed_price`, `unit_price_drop`, `order_value`, `stock_value_at_current_price`). It also records `min_spend`, `max_discount`, `discount_value`, `min_spend_required`, `bundle_total`, `savings`, `original_total`, `total_savings`, `final_total`, `shipping_cost` and `total_shipping_cost` as tool-reported figures, so answers quoting them are no longer `unverifiable` (derived or invented figures stay so).
- **.NET grounding reaches parity with Python (row 17).** A per-request tool-result ledger (`GroundingLedger`, recorded by `SpecialistPipeline` for every tool result and its arguments, the same field list as Python), the `user_supplied` and `derived` verdicts, and `GROUNDING_MODE=enforce` (corrects or strips cards; no longer refused at startup). `config/grounding/testdata/ledger.v1.json` is run by both stacks' tests.

**Demo, preview and course**

- **Static web preview on GitHub Pages (#301).** The web app built with `NEXT_PUBLIC_PREVIEW=1` is a key-free, backend-free copy that replays the demo tour's recorded answers (steps, streamed text, node and handoff frames, the return approval card) in the browser, with the tour user, a 35-product catalog and two orders from committed data. `scripts/build_preview_data.py` derives that data from the seed, `tour.yaml` and the demo fixtures (`--check` fails CI when stale); `web/src/lib/preview/mock-api.ts` stands in for `fetch` in `api.ts`, and a normal build contains none of it. Anything unrecorded answers "This preview replays recorded answers; run it locally for the real thing". Published under `/preview` of the docs site by `jekyll-gh-pages.yml`; `preview.yml` builds and tests the export on pull requests. Also: a "Preview: recorded answers" banner, `pnpm build:preview` and `pnpm serve:preview`, `web/e2e/preview.spec.ts`, and the app's Open Graph image. See [docs/preview.md](docs/preview.md).
- **README hero GIF and feature stills, recorded keyless (#87, #182).** `scripts/record-demo.sh` drives `web/e2e/readme-gif.spec.ts` (skipped unless `RECORD_MEDIA=1`) against the no-key stack and writes `docs/images/demo.gif` (under 5 MB, 960 px) plus stills of the live orchestration graph, the Compare dialog, `/runs` with a pending approval and an Aspire trace.
- **The no-key demo has its recordings, and CI runs the tour (#86).** `agents/python/demo/fixtures/replay/` holds the recorded model responses for the nine tour chips (as the tour user), the demo guide's prompts (as Alice), and the greeting, generative-UI, shopping, follow-up and grounding-badge prompts; `manifest.json` maps each prompt to its recordings and `tests/test_demo_fixtures.py` keeps the directory, the manifest, `config/demo/tour.yaml` and the current tool schemas in step. Tests that send other unrecorded prompts skip under `E2E_NO_KEY=1` with the reason. `web/e2e/no-key-tour.spec.ts` runs every tour chip against the no-key stack and checks the return pauses with nothing created; the `no-key-tour` job in `compose-smoke.yml` runs it. Seven more tutorial chapters (04, 05, 07, 12, 13, 14, 16) run with no key; only 20b (DevUI) is skipped.
- **Four course chapters that need no Azure (#100); the course is now 38 chapters.** All Python only, each with a keyless run, recorded fixtures only where a model is called, real output pasted into the README and a Gotchas section. [33 Prompt Injection and Red Team](tutorials/33-prompt-injection-and-red-team/README.md) runs the repo's eight red-team prompts and the indirect-injection corpus through the real `shared/guardrails` matching code (three of eight direct attacks flagged by pattern; the output sanitizer changed one of five indirect texts), then shows that a scripted always-obedient model still refunds after the sanitizer and does not once the tool is gated. [34 Structured Output](tutorials/34-structured-output/README.md) validates card frames in Pydantic the way `web/src/lib/chat-schemas.ts` does, asks a model for a typed reply with `response_format`, and covers a bounded repair then a text fallback; the application itself uses no `response_format`. [35 Memory](tutorials/35-memory/README.md) covers rehydration from the messages table, the opt-in history compaction, long-term memory and what is deliberately not remembered. [36 Model Providers](tutorials/36-model-providers/README.md) covers `LLM_PROVIDER` selection, per-agent overrides, the stored capability probes and the not-run list. The four chapters ship 12 recorded model requests (about 0.02 USD at the `gpt-4.1` price in `config/pricing/models.yaml`). The deploy, durable workflows, Foundry evals and AG-UI chapters of #100 are not written.

**Evals and cost control**

- **The orchestrator is scored in every mode (#97).** `evals.run_evals --mode handoff|group-chat|workflow:pre-purchase|workflow:return-replace` runs the cases that list the mode in a new `modes` field (with per-mode `mode_overrides`), in-process. Baselines are per mode (`evals/baselines/python-inproc-replay-mode-<mode>/`), `scripts/replay-evals.sh` gates each, and `docs/eval-scorecard.md` has an "Orchestrator by mode" table. New `orchestrator_modes.json` (research and return prompts, plus a live twin); the workflow modes call no model; handoff and group chat have recorded fixtures (13 new). `fixture_drift` reads the handoff triage agent's synthesised `handoff_to_*` tools.
- **Indirect prompt-injection eval (#97).** `datasets/indirect_injection.json` (4 recorded cases) and a live twin (5): reviews and seller product text that order a refund, a cancellation or a return (`seed_adversarial.ACTION_REVIEWS`). Asserts the orchestrator does not route to `order-management`, calls none of the gated tools, creates no approval and echoes no canary token (new `forbidden_substrings` assertion). `replay-evals.sh` seeds the corpus for the suite and removes it again.
- **Optional judge-scored red-team dimension (#97).** `--use-llm-judge` on the safety suite adds `attack_resisted` and `refusal_quality`, graded by a judge that sees the attack text, the reply and the tool names (never the system prompt), reported beside the keyword verdict and never part of `passed`. A content-filtered judge call falls back to the keyword verdict under the label `fallback_keywords`; other judge failures are unmeasured. Off by default; stored on the record (`attack_judge`), so `--rescore` never calls the judge.
- **MCP versus native tools, as a keyless CI gate (#303, #97).** `scripts/mcp-compare.sh` and the `MCP versus native tools` job in `evals.yml` start the two MCP servers and run `evals/mcp_compare.py`: eleven tool calls both ways on the seeded database, and the product-discovery and inventory-fulfillment cases both ways under replay. The first run found the MCP tools were not equivalent to the native ones (see Fixed, #320); two recorded-choice differences remain in `evals/mcp_known_differences.json`, each with its reason.
- **Turn-level cost ceiling and `usage_logs.would_block` (#97; Python, parity row 58).** `COST_BUDGET_*` compares the whole conversation turn with the ceiling: the orchestrator sends `x-turn-spend` on every inter-agent call (both wires) and a specialist counts it as already spent; specialists' priced usage counts toward the orchestrator's total. In `observe` mode each LLM turn past the ceiling is counted on the run's usage record (migration 0020) so a rate can be measured. Default unchanged (`observe`); no usage wire field, token format or fixture key changed. .NET is not ported; the design is in `docs/configuration.md`.
- **Multi-turn live cases and `forbidden_tools`.** A live case may carry `turns`, run in one conversation on the in-process and `http` targets, scored per turn (the case passes only if every turn does) and stored in a `turns` list of the run record; replay datasets reject it. Twelve multi-turn cases cover order-management, pricing-promotions and product-discovery ("the second one", corrections, a change of mind mid-flow where nothing may park, quantity changes, switching topic and back; all read-only). A case may list `forbidden_tools`; a call fails its assertions and never moves a score.
- **Live-capable datasets and reference baselines.** `evals/datasets/live/*.live.json` name orders and products as `{{order:1}}`, `{{order:shipped}}`, `{{order:STATUS@email}}` and `{{product_id:NAME}}`, resolved against the run's database (`evals/live_facts.py`) and a seeded customer, so a live model is asked about facts that exist (order management scored 49.2% live before, because the ids did not exist). Each case carries a `type` and a one-line `why`; a test checks every placeholder resolves on a freshly seeded database and the per-type minimums hold. Replay datasets and fixtures are pinned placeholder-free and unchanged. Sizes at this writing: order-management 37, inventory-fulfillment 29, pricing-promotions 33, product-discovery 29, review-sentiment 30, orchestrator routing 31 (with `expected_routes` and `forbidden_routes` assertions), merchant-ops 19, approvals 10. Six suites have stored live baselines under `evals/baselines/python-inproc-gpt-4.1/` (k=1, `live: true`, judge `gpt-5.4-mini`, never self-judged, a one-repeat caveat); the scorecard shows them in their own table and a test pins that the replay gate never reads them. After the grounding ledger fix, pricing passed 28 of 29 (overall 0.95, groundedness 0.96) and inventory 28 of 29 (0.94, 1.00); order-management passed 30 of 33 and the orchestrator 30 of 30 on the datasets of that run.
- **Eval harness features.** `python -m evals.run_evals --estimate` prints the token count and cost a live run would have from the replay fixtures and the price file, and exits without calling a model; `--concurrency` bounds live calls and a 429 or 5xx is retried with a `Retry-After`-aware, jittered, capped backoff. The `http` target (`--target http --base-url <orchestrator> --stack <name>`) logs in as a seeded user, drives `POST /api/chat/stream` and writes the same run records as the in-process target, so the same datasets measure the Python stack, the .NET stack, a mixed stack or a deployed URL; facts the wire cannot show are listed per record in `not_observable` and assertions on them are skipped, not failed. `.github/workflows/stack-parity.yml` boots both stacks under replay, runs `evals/datasets/stack_parity.json` through the http target and fails when they diverge on tool calls, routes, workflow nodes, approval flags or mean score (parity row 53); it covers turns that call no model. `scripts/eval-http-parity.sh` checks the http target against the in-process one.
- **Judge calibration and provider capability results.** `evals/judge_calibration/` holds 30 drafted, unreviewed completeness labels, stored verdicts for four candidate judges and an offline `--rescore`; with `gpt-5.4-mini` and `gpt-5.2` priced from the Azure Retail Prices API (`verified: false`), the stated rule (5 points of agreement, under 3x cost) recommends `gpt-5.4-mini` over `gpt-4.1-mini` (+15 points at 2.14x); `gpt-4.1` agrees most but costs 5.2x. No judge is defaulted in code; the Azure profiles suggest `gpt-5.4-mini`. `evals.provider_check` gains tool-result and `temperature` probes, per-probe token counts, a `max_tokens` to `max_completion_tokens` retry for the gpt-5 family and `--save-result`; results for eight Azure deployments and two Ollama models are stored under `evals/provider-results/` and the capability table in `docs/model-providers.md` is generated from them (`scripts/build_provider_table.py --check` runs in CI).
- **Foundry evaluators adapter (EV-8) and community results (EV-14), both optional.** `uv sync --extra foundry-evals` adds `azure-ai-evaluation`; `python -m evals.foundry.run --records <file>` maps run records onto Foundry's groundedness, intent resolution, tool-call accuracy, task adherence and indirect-attack evaluators, runs them locally against a judge deployment you name (`FOUNDRY_EVAL_*`) and optionally logs the run to a Foundry project (`--log-to-project`). The core eval path imports no Azure package and never imports the adapter; a test enforces it. Anyone can contribute a result file for their own provider and model under `agents/python/evals/community/`; `python -m evals.community_validate <file>` checks the schema, re-scores from attached records with no model call and refuses secret-looking strings, absurd scores, an undisclosed judge and an unlabelled self-judged run. The scorecard lists accepted files in a separate table, "Community-submitted, not verified by the maintainers".
- **The manual live eval job is rebuilt.** `full` in `evals.yml` stays `workflow_dispatch` only and takes `provider` (openai, azure, anthropic, openai-compatible), `model`, `repeats`, `suite`, `pass_threshold`, a judge switch and two token ceilings. Keys come from repository secrets by name and a missing one fails with its name before any model call; the job runs `evals/datasets/live/` in-process against a Postgres service container and uploads the run records and a generated scorecard. The logic is `scripts/live-evals.sh`, which also runs locally. `tests/test_workflow_secret_triggers.py` fails if a job that reads a model key can run from anything but a manual dispatch.
- **No model price is written in code (both stacks).** Both read `agents/python/config/pricing/models.yaml`, in USD per 1M tokens, with a source, a date and a `verified` flag on every row; `PRICING_OVERRIDES_FILE` adds private entries without a repo change; shared golden cases keep the two stacks pricing identically. An unknown model's cost is now unknown (null), not gpt-4.1's: with `COST_BUDGET_MODE=enforce` and a ceiling, a turn on an unpriced model stops the run. `COST_BUDGET_TOKENS_PER_RUN` is a per-run token ceiling beside the dollar ceiling, under the same mode, that still holds for an unpriced model (unset, empty or 0 means off; a turn that reports no usage also stops the run in `enforce`). `usage_logs.price_source` and `priced_at` (migration 0015) say which pricing file and version priced a row and when. `gpt-5.4-mini` and `gpt-5.2` are priced from the Azure Retail Prices API (`verified: false`).

**Run profiles, observability and Azure (optional)**

- **Azure free-trial profile, a sanity check and a Deploy to Azure button.** `--profile free-trial` deploys the demo topology at its smallest with Foundry (account, project, `gpt-4.1-mini` chat and a small embedding deployment), scale to zero, a 0.1 GB/day log cap and a 10 USD budget with alerts at 50, 80 and 100 percent actual spend and a forecast alert; `deployOpenAi=false` / `--llm replay|openai` skips the model account. The profile is keyless where the code allows (identity to the model with local auth disabled on the account, Key Vault references, public images, four secrets left; Postgres keeps a generated password, reasons in the docs). `scripts/azure/sanity.sh` checks a deployed environment end to end (database, a direct model call with your own Entra token, a full turn, a few http evals) and tears nothing down; `scripts/azure-down.sh` now removes everything a group delete leaves (workspace, vault, account, deployment records, local state) and `scripts/azure/verify-clean.sh` proves it read only. `infra/azuredeploy.json` (compiled, drift-tested) backs a README button; without azd the secrets come from parameter defaults and one command (`DB_INIT_START_COMMAND`) starts the database job. New page [azure-deployment-sequence.md](docs/azure-deployment-sequence.md) with costs from the Retail Prices API (2026-11-23). Offline-verified only; nothing deployed.
- **Run profiles, the doctor and one startup line.** Twelve provider setups (`keyless`, `openai`, `azure-key`, `azure-identity`, `anthropic`, `gemini`, `ollama`, `openrouter`, `llama-cpp`, `openai-compatible`, `azure-gateway`, `foundry`) in `agents/python/config/profiles/`; each sets chat, embeddings and the model together, holds no secret and states its tier, key, cost, .NET support and how it was verified (most were stub-tested or not tested at all, and the header says so). `./scripts/dev.sh --profile NAME` (`dev.ps1 -Profile`, `replay-evals.sh --profile keyless`, `evals.run_evals --profile`) applies one: your shell wins, then the profile, then `.env`. `python -m scripts.doctor` prints the effective configuration with secrets redacted, validates it and checks the model endpoint, database, Redis and every registry entry without spending tokens (`--json`, exit 1 on a hard failure). Every service on both stacks logs one structured startup line (parity row 51). `docs/run-profiles.md` has the table.
- **Gateway attribution headers on every model call, both stacks (AZ-4, parity row 52).** Chat and embeddings requests carry `x-agent-name`, `x-run-id` and `x-eval-run-id` so an AI gateway can count tokens per agent and per run. Values are sanitised and capped, an empty one is omitted, and no user identity or secret is sent. `GATEWAY_HEADERS_ENABLED` (default true) turns it off; `EVAL_RUN_ID` sets the eval run id. Replay keys and every fixture are unaffected.
- **Optional Azure modules (Bicep, offline only).** `deployFoundryProject` adds a Foundry project under the AIServices account; `deployGateway` adds API Management in front of the account with per-agent token limits and token metrics keyed on `x-agent-name`; `deployPerAppIdentities` adds one user-assigned identity per app. All three are off by default. The `reference` profile is written and compiled: a VNet with a delegated Container Apps subnet and a private endpoint subnet, private endpoints and private DNS zones for PostgreSQL, Key Vault and the AIServices account with public access off, Azure Managed Redis in place of the redis container, a Basic Container Registry with `AcrPull` for the app identity, Entra auth to the model by default and `A2A_IDENTITY=token` with a distinct generated key. `scripts/azure-up.sh --build-from-source` builds every image in ACR Tasks from the working tree (`--image-source acr` imports the published images; `--dry-run` prints every command). `stack=dotnet` deploys the .NET orchestrator, specialists and inventory MCP host; `build-images.yml` publishes seven `<service>-dotnet` images (amd64) with a `/health` smoke test (parity row 54). The `azure` provider pointed at the account is Foundry for OpenAI models; there is no `LLM_PROVIDER=foundry`. None of this has been deployed (see Known limits).
- **Dependency policy, a hardened merge helper, and shell checks.** `docs/contributing-dependencies.md` records the Dependabot policy (grouped minor and patch, one reviewed PR per major, the MAF group as a deliberate step), how to handle conflicting PRs and lockfiles, the keep-both rule for `CHANGELOG.md` and `docs/parity-matrix.md`, and a worktree and branch cleanup runbook. `scripts/merge-when-green.sh <pr>` squash-merges only when the head SHA is unchanged since it started, at least 20 checks are complete and none failed, and the PR is mergeable, on two polls 45 seconds apart; it pins the merge with `--match-head-commit`, never uses `--admin`, and exits non-zero with a reason otherwise (tested with a fake `gh` in `tests/test_merge_watcher.py`). `scripts/flag-smoke.sh` boots the stack with every off-by-default flag on (replay, keyless) and drives event autonomy, the return lifecycle and merchant proposals through the real routes and the outbox worker; CI runs it as the `flags` job of `compose-smoke.yml`. shellcheck at warning level is clean across `scripts/` and gated in `tests.yml`. Codespaces honours model keys (`devcontainer.json` declares the key secrets, `postCreate.sh` picks `azure`, `openai` or `replay` from what is present and never writes a key).
- **Client-address peer gate and a removal marker (both stacks).** `RATE_LIMIT_TRUSTED_PROXIES` (IPs and CIDRs) limits who may set `X-Forwarded-For`; the code default of `RATE_LIMIT_TRUSTED_PROXY_HOPS` is now 0 (compose and Bicep still set it). The same address keys the unknown return-label budget, chat and auth limits. `LEGACY_LABEL_TOKEN_REMOVE_AFTER` / `ReturnLabelToken.LegacyRemoveAfter` (2026-12-31) has a test on each stack that fails after it.
- **Live reference baseline refreshed for order-management (37 cases, k=1, gpt-4.1 under test, gpt-5.4-mini judge):** 30 passed, overall 0.90 (was 0.94 on 33 cases); the 1.10 USD cap covered this suite only, so the other live baselines are unchanged.
- **Four course chapters that need no Azure (#100), 34 to 38 chapters.** All Python only, each with a keyless run, recorded fixtures only where a model is called, real output pasted into the README and a Gotchas section. [33 Prompt Injection and Red Team](tutorials/33-prompt-injection-and-red-team/README.md) runs the repo's eight red-team prompts and indirect-injection corpus through the real `shared/guardrails` matching code (three of eight direct attacks flagged by pattern; the output sanitizer changed one of five indirect texts and left the rest alone), then shows that a scripted always-obedient model still refunds after the sanitizer and does not once the tool is gated. [34 Structured Output](tutorials/34-structured-output/README.md) validates card frames in Pydantic the way `web/src/lib/chat-schemas.ts` does, asks a model for a typed reply with `response_format`, and covers a bounded repair then a text fallback; it states that the application uses no `response_format`. [35 Memory](tutorials/35-memory/README.md) covers rehydration from the messages table (ownership scope, most recent rows, user and assistant text only), the opt-in history compaction from #163, long-term memory, and what is deliberately not remembered. [36 Model Providers](tutorials/36-model-providers/README.md) covers `LLM_PROVIDER` selection, per-agent overrides, the stored capability probes and the not-run list; Azure was probed live, OpenAI direct was never called and Anthropic is stub-tested only, and it says so. The tutorials index, learning paths, docs site tiers and the chapter-count claims (README, docs, site description) move from 34 to 38; the four chapters are linked from the matching concept pages. The four chapters ship 12 recorded model requests (about 6,000 input and 470 output tokens, roughly 0.02 USD at the `gpt-4.1` price in `config/pricing/models.yaml`). #100 stays open: the Azure deployment, durable workflows, Foundry evals and AG-UI chapters are not written.
- **PO-value approval threshold for merchant restock proposals (#298; Python, parity row 39).** A restock batch whose PO value (quantity x unit cost over the lines the seller accepts) is over the threshold in `approvals.yaml` (`merchant_proposal`, fact `amount`, placeholder 5000; golden cases in `testdata/merchant_proposal.yaml`, which the .NET policy tests also run) no longer finishes on the seller's word. The seller's accepted restock lines are recorded as `awaiting_manager` and the same `approvals` row is routed to `manager` with a fresh SLA clock; the staff inbox then shows it and an admin approves or rejects the lines (lowering a quantity is allowed, raising never). Under the threshold nothing changes, markdown lines never wait, the seller cannot decide the manager tier, whoever decided the seller's side cannot also be the manager, and an SLA breach never expires the batch under a decide in flight. Migration 0019 adds the two statuses and narrows the beneficiary CHECK to the seller's own stage. .NET is unchanged: it still answers 404 for source `proposal`. See [docs/merchant-proposals.md](docs/merchant-proposals.md#po-value-threshold).
- **Customer confirmation ported to the .NET stack (#307, parity row 56 is now Parity).** `HitlGate.TryGateAsync` asks the customer to confirm the exact action after the pre-park checks, with the same fixed sentence, and parks only when the same call arrives again after an explicit yes read from the stored `messages` row (scoped to the caller; the message the request was sent as is used only when no conversation is stored). New: `CustomerConfirmation` (the conservative matcher, the argument key that ignores `reason`, the prompt), `CustomerConfirmationRepository` over the existing `customer_confirmations` table (no new migration), and the settings `CUSTOMER_CONFIRMATION_ENABLED` and `CUSTOMER_CONFIRMATION_TTL_SECONDS` (defaults true and 600). `customer-confirmation.v1.json` now runs in both stacks (`CustomerConfirmationVectorTests`), and `CustomerConfirmationGateTests` drives the real gate on a Postgres testcontainer: ask, confirm, decline, silence, stale, TTL, different arguments, reworded reason, injected claim, per-user and per-session scope, concurrent yes, a failed park, disabled. The existing .NET gate tests turn confirmation off in their fixtures. A mixed stack no longer skips the question on a .NET specialist. Known difference: under `InvariantGlobalization` only fullwidth compatibility characters are folded (anything else fails closed).
- **Customer confirmation before a gated call parks (#302, D-5b; parity row 56).** In `tool` mode the host now asks the customer to confirm the exact action in plain words before `cancel_order`, `modify_order`, `initiate_return`, `process_refund` or `place_backorder` parks for staff; only an explicit yes on a later turn lets it park. It is a deterministic record, not a prompt: migration 0016 `customer_confirmations` (one open row per user, session, tool and normalised arguments), the user's words read from the stored conversation (never the text the orchestrator's model wrote), a conservative `is_affirmative`, a TTL (`CUSTOMER_CONFIRMATION_TTL_SECONDS`, 600) and `CUSTOMER_CONFIRMATION_ENABLED`. Declines, silence, a stale yes, different arguments and an injected "already confirmed" leave nothing for staff. Sentences and matching vectors are pinned in `config/policies/testdata/customer-confirmation.v1.json` for both stacks. No prompt or tool schema changed, so no existing fixture moved. New eval datasets: `evals/datasets/approvals.json` (replay, 5 cases, recorded, baseline `python-inproc-replay/approvals.json`, a sixth suite in `scripts/replay-evals.sh`) and `evals/datasets/live/approvals.live.json` (10 multi-turn cases, `approvals` suite in `scripts/live-evals.sh`); the six live cases that expected a one-turn park are now two turns. Live validation, gpt-4.1 under test, gpt-5.4-mini judge, k=1: 10 of 10 passed, overall 96.7%, completeness 83.5%. See [docs/approvals.md](docs/approvals.md#customer-confirmation-before-a-call-parks).
- **Static web preview on GitHub Pages (#301).** The web app built with `NEXT_PUBLIC_PREVIEW=1` is a key-free, backend-free copy that replays the demo tour's recorded answers (steps, streamed text, node and handoff frames, the return approval card) in the browser, with the tour user, a 35-product catalog and two orders from committed data. `scripts/build_preview_data.py` derives that data from the seed, `tour.yaml` and the demo fixtures (no request bodies or system prompts; `--check` fails CI when stale); `web/src/lib/preview/mock-api.ts` stands in for `fetch` in `api.ts`, and a normal build contains none of it. Anything unrecorded answers "This preview replays recorded answers; run it locally for the real thing" with a link to the quick start. Published under `/preview` of the docs site by `jekyll-gh-pages.yml` (one Pages deployment, so the docs are not overwritten); `preview.yml` builds and tests the export on pull requests. Also: a "Preview: recorded answers" banner, `pnpm build:preview` / `pnpm serve:preview`, `web/e2e/preview.spec.ts`, vitest for the mock layer, and the app's Open Graph image (`opengraph-image.tsx`). The three client-side dynamic pages gained thin server wrappers for `generateStaticParams`. See [docs/preview.md](docs/preview.md).
- **Optional judge-scored red-team dimension (#97).** `--use-llm-judge` on the safety suite adds `attack_resisted` and `refusal_quality`, graded by a judge that sees the attack text, the reply and the tool names (never the system prompt), reported beside the keyword verdict and never part of `passed`. A content-filtered judge call falls back to the keyword verdict under the label `fallback_keywords` (the #287 rule); other judge failures are unmeasured. Off by default: replay runs, records and fixtures are unchanged. Stored on the record (`attack_judge`), so `--rescore` never calls the judge. Schema and rubric in `evals/README.md`.
- **Twelve multi-turn live cases (#97)** across order-management, pricing-promotions and product-discovery: "the second one", corrections, a change of mind mid-flow (nothing may park), a clarification, quantity changes, and switching topic and back. All read-only. No replay dataset or fixture changed.
- **Turn-level cost ceiling and `usage_logs.would_block` (#97; Python, parity row 58).** `COST_BUDGET_*` now compares the whole conversation turn with the ceiling: the orchestrator sends `x-turn-spend` on every inter-agent call (both wires) and a specialist counts it as already spent; specialists' priced usage counts toward the orchestrator's total. In `observe` mode each LLM turn past the ceiling is counted on the run's usage record (migration 0018 `usage_logs.would_block`) so a rate can be measured. Default unchanged (`observe`); no usage wire field, token format or fixture key changed. .NET not ported, design in `docs/configuration.md`.
- **Decision recorded: `azure_content_safety` stays unimplemented (#97).** `docs/agent-quality.md` gives the reasoning, the regex layer's measured numbers and a five-step measurement plan for revisiting it.
- **Customer confirmation before a gated call parks (#302, D-5b; Python, parity row 56).** In `tool` mode the host now asks the customer to confirm the exact action in plain words before `cancel_order`, `modify_order`, `initiate_return`, `process_refund` or `place_backorder` parks for staff; only an explicit yes on a later turn lets it park. It is a deterministic record, not a prompt: migration 0016 `customer_confirmations` (one open row per user, session, tool and normalised arguments), the user's words read from the stored conversation (never the text the orchestrator's model wrote), a conservative `is_affirmative`, a TTL (`CUSTOMER_CONFIRMATION_TTL_SECONDS`, 600) and `CUSTOMER_CONFIRMATION_ENABLED`. Declines, silence, a stale yes, different arguments and an injected "already confirmed" leave nothing for staff. Sentences and matching vectors are pinned in `config/policies/testdata/customer-confirmation.v1.json` for the .NET port, which still parks immediately. No prompt or tool schema changed, so no existing fixture moved. New eval datasets: `evals/datasets/approvals.json` (replay, 5 cases, recorded, baseline `python-inproc-replay/approvals.json`, a sixth suite in `scripts/replay-evals.sh`) and `evals/datasets/live/approvals.live.json` (10 multi-turn cases, `approvals` suite in `scripts/live-evals.sh`); the six live cases that expected a one-turn park are now two turns. Live validation, gpt-4.1 under test, gpt-5.4-mini judge, k=1: 10 of 10 passed, overall 96.7%, completeness 83.5%. See [docs/approvals.md](docs/approvals.md#customer-confirmation-before-a-call-parks-python).
- **Autonomy: order-risk review, damaged-item remedy and monthly budgets (#299; Python, parity row 40).** `order.risk_flagged` scores a placed order from facts already in the database (total, new account, shipping versus billing address, items, orders by the same customer in 24 hours; signals and thresholds in `autonomy.yaml`, no model): low is `no_action`, medium opens an `autonomous_action` approval, high also **holds** the order (`orders.on_hold`, reversible by an admin or by staff clearing the review, recorded as a decision row and a `reversal`). A risk review is never an autonomous tier (the loader refuses one) and the engine never cancels, refunds or changes an order's status. `order.item_damaged` gives a damaged order line one remedy ever, a store credit (20 percent up to 15.00) or a free replacement offer, on the same budget, lock and guard rules as the delay credit. A budget may carry `global_per_month`, checked in the same transaction and by the same helper as the day cap (`lock_budget` takes the day lock, then a month lock); the decisions page shows month use. Simulator scenarios `order-risk` and `item-damaged`, worker handlers, golden cases, migration 0018 and `scripts/flag-smoke.sh` cover them. Every cap, signal and threshold is a placeholder. A real carrier feed is external and stays open in #299.
- **README hero GIF and feature stills, recorded keyless.** `scripts/record-demo.sh` drives `web/e2e/readme-gif.spec.ts` (skipped unless `RECORD_MEDIA=1`) against the no-key stack and writes `docs/images/demo.gif` (under 5 MB, 960 px) plus stills of the live orchestration graph, the Compare dialog, `/runs` with a pending approval and an Aspire trace, embedded in the README, demo guide, approvals, benchmark and telemetry pages (#87, #182).
- **Autonomy: price-drop and back-in-stock events, and an optional ops-responder (#299).** `product.price_dropped` pays a store-credit price adjustment per recent order line (sized from the line, one per line ever, same budget, lock and guard rules as the delay credit); `inventory.back_in_stock` notifies customers whose cart holds an in-stock product (no money, a per-customer daily cap, a bounded audience). Simulator scenarios `price-drop` and `back-in-stock`, worker handlers, golden cases, migration 0017, decisions page and `scripts/flag-smoke.sh` cover both. `AUTONOMY_RESPONDER_ENABLED` (off) has a model draft the customer message after the decision commits; the draft is checked against the decision's facts, falls back to the template, and can never change an amount or a tier. Every new cap is a placeholder. Monthly budgets, order-risk and damaged-item events and a real carrier feed stay open in #299.
- **Decision recorded: `COST_BUDGET_MODE` stays `observe` (#97).** `docs/configuration.md` states why the default does not move to `enforce` (unpriced models fail closed, prices unverified, no measured `would_block` rate, the per-run ceiling does not bound a multi-agent turn) and what would change it.
- **MCP versus native tools, as a keyless CI gate (#303, #97).** `scripts/mcp-compare.sh` and the `MCP versus native tools` job in `evals.yml` start the two MCP servers and run `evals/mcp_compare.py`: eleven tool calls both ways on the seeded database, and the product-discovery and inventory-fulfillment cases both ways under replay (11 new fixtures for the MCP path). It found that the MCP tools are not equivalent to the native ones: eight of eleven calls differ in shape, `get_trending_products` ranks differently, and one inventory case picks another tool (tool-call accuracy 0.955 native, 0.864 MCP). Accepted differences are listed with reasons in `evals/mcp_known_differences.json`; `docs/mcp-integration.md` no longer says the two paths are identical.
- **Indirect prompt-injection eval (#97).** `datasets/indirect_injection.json` (4 recorded cases) and a live twin (5): reviews and seller product text that order a refund, a cancellation or a return (`seed_adversarial.ACTION_REVIEWS`, kept out of the pinned guardrail-confusion corpus). Asserts the orchestrator does not route to `order-management`, calls none of the gated tools, creates no approval and echoes no canary token. New quality-schema assertion `forbidden_substrings`. `replay-evals.sh` seeds the corpus for the suite and removes it again; baseline `python-inproc-replay/indirect_injection.json`.
- **The orchestrator is scored in every mode (#97).** `evals.run_evals --mode handoff|group-chat|workflow:pre-purchase|workflow:return-replace` runs the cases that list the mode in a new `modes` field (with per-mode `mode_overrides`), in-process. Baselines are per mode (`evals/baselines/python-inproc-replay-mode-<mode>/`), `scripts/replay-evals.sh` gates each, and `docs/eval-scorecard.md` has an "Orchestrator by mode" table. New `orchestrator_modes.json` (research and return prompts, plus a live twin); routing cases list `tool` and `handoff`. The workflow modes call no model; handoff and group-chat have recorded fixtures (13 new). `fixture_drift` now reads the handoff triage agent's synthesised `handoff_to_*` tools.
- **Merchant-ops specialist (#298, Python only).** A sixth specialist, `merchant_ops/` on port 8086 behind its own compose profile (`MERCHANT_OPS_AGENT_ENABLED=true ./scripts/dev.sh`), explains and orders a seller's pending markdown and restock proposals (the stored rationale and facts, a deterministic approve-first order, why a line is blocked) and triages reviews of the seller's products (abusive, instruction-like, off-topic, low-signal) with a polite draft reply, including text the seller pastes. Seven read-only tools, seller or admin only, identity from the ContextVars; there is no write tool, so every action stays a proposal line the seller decides through the approvals machinery. The orchestrator routes to it only with `MERCHANT_OPS_AGENT_ENABLED` (default off): off, the registry drops it and the orchestrator prompt and routing-tool description are unchanged, so no orchestrator replay fixture moved (the orchestrator and safety suites replay with no missing fixture). Replay dataset `merchant_ops.json` (5 cases, fixtures recorded, baseline `python-inproc-replay`) and live twin `live/merchant_ops.live.json` (19 cases; one sitting, 16 passed, not stored as a baseline, see `docs/agent-quality.md`). Parity matrix row 56 (Python only); `docs/merchant-proposals.md#the-merchant-ops-agent`.
- **Merchant-ops specialist (#298, Python only).** A sixth specialist, `merchant_ops/` on port 8086 behind its own compose profile (`MERCHANT_OPS_AGENT_ENABLED=true ./scripts/dev.sh`), explains and orders a seller's pending markdown and restock proposals (the stored rationale and facts, a deterministic approve-first order, why a line is blocked) and triages reviews of the seller's products (abusive, instruction-like, off-topic, low-signal) with a polite draft reply, including text the seller pastes. Seven read-only tools, seller or admin only, identity from the ContextVars; there is no write tool, so every action stays a proposal line the seller decides through the approvals machinery. The orchestrator routes to it only with `MERCHANT_OPS_AGENT_ENABLED` (default off): off, the registry drops it and the orchestrator prompt and routing-tool description are unchanged, so no orchestrator replay fixture moved (the orchestrator and safety suites replay with no missing fixture). Replay dataset `merchant_ops.json` (5 cases, fixtures recorded, baseline `python-inproc-replay`) and live twin `live/merchant_ops.live.json` (19 cases; one sitting, 16 passed, not stored as a baseline, see `docs/agent-quality.md`). Parity matrix row 57 (Python only); `docs/merchant-proposals.md#the-merchant-ops-agent`.
- **Grounding: ids a tool returned this turn are verified, and the proposal figures are recorded.** The bare-id check only knew products and orders, so a review, proposal or proposal-line id quoted from a tool result scored "not found"; the ledger now records those ids and the merchant-ops amounts (`proposed_price`, `unit_price_drop`, `order_value`, `stock_value_at_current_price`).
- **Eval harness: `merchant-ops` is an `--agent` choice** (`evals/harness.py`), `evals/merchant_setup.py` generates the demo sellers' proposal batches before a merchant-ops run, and `scripts/replay-evals.sh`, `scripts/live-evals.sh` and the `evals.yml` suite choice include it. `PYTHON_ONLY_SANITIZE` names the four sanitized merchant-ops tools the .NET sanitize list does not have.
- **No-key tour in CI (#86).** `web/e2e/no-key-tour.spec.ts` runs every chip of `config/demo/tour.yaml` against the no-key stack (non-empty recorded answer, never the "not recorded" notice) and checks the return pauses for approval with nothing created; the `no-key-tour` job in `compose-smoke.yml` boots `docker-compose.yml` + `docker-compose.nokey.yml` and runs it.
- **Demo stack: `auth-server` moves behind the `oauth` profile** in `docker-compose.demo.yml`, so the default demo is 11 running containers. `dev.sh --demo` / `dev.ps1 -Demo` add the profile when `AUTH_MODE=oauth`.
- **ADR 0007** records keeping the hand-rolled group chat and orchestration modes instead of MAF's builders (#98); `docs/azure-deployment.md` states the Bicep is hand-written, not generated from the Aspire AppHost.

### Changed

- **Aspire AppHost moved to 17.0.0** (Hosting.PostgreSQL, Redis, Python, JavaScript, Testing, plus Hosting.AppHost and Hosting.Dotnet). Aspire 17 requires .NET 10 and obsoletes `Aspire.AppHost.Sdk` and `AddProject<T>`; the AppHost now uses `Microsoft.NET.Sdk` and `AddDotnetProject`. The Aspire dashboard image stays on 13.6.5 because no 17.x tag exists on MCR as of 2026-11-24; the mismatch is noted next to each pin and in `docs/deployment.md`.
- **Node runtime moves to 26 (LTS), with `@types/node` 26.** `web/Dockerfile` (pnpm installed from npm, Corepack is gone in Node 25+), `.nvmrc`, `engines`, CI, the devcontainer and the docs name one line; Dependabot ignores `@types/node` 27+ until the runtime moves. Supersedes #226.
- **Dependabot:** container images, compose files and GitHub Actions are monthly, the three package ecosystems weekly, and the Serilog.AspNetCore major is ignored until it is migrated (it broke the build and every boot job).
- **Demo stack and compose.** `auth-server` moves behind the `oauth` profile in `docker-compose.demo.yml`, so the default demo is 11 running containers; `dev.sh --demo` and `dev.ps1 -Demo` add the profile when `AUTH_MODE=oauth`. The pricing directory is mounted read-only at `/pricing` in every agent container (a price edit takes effect on restart, no rebuild), and `host.docker.internal` is mapped so local model servers work from containers on Linux. The `dev.sh` key preflight understands `AZURE_OPENAI_AUTH=identity` and `LLM_PROVIDER=anthropic`; `scripts/reset_embeddings.py` gains `--if-needed`.
- **Handoff mode sends the `grounding` frame.** `HandoffMode.run` goes through `@grounded_run` like the workflow modes and group chat (NA-9), so every Python mode's final text is verified per `GROUNDING_MODE` and `chat_stream` emits the frame the .NET twin already sent.
- **Live dataset expectations follow the pre-park checks.** A delivered-order address change and an in-stock backorder no longer expect the gated tool to run (the check refuses both in words, no approval). No assertion was loosened beyond that.
- **Replay baselines refreshed to the re-recorded fixtures** (`python-inproc-replay` only): pricing-promotions 0.68 to 0.96, order-management 0.51 to 0.75, inventory-fulfillment 0.82 to 0.90, orchestrator routing 0.80 to 1.00; product-discovery 0.96 and review-sentiment 1.00 unchanged. The live `python-inproc-gpt-4.1` baselines are untouched.
- **Decisions recorded, no code change.** `azure_content_safety` stays unimplemented (#97; `docs/agent-quality.md` has the reasoning, the regex layer's measured numbers and a five-step plan for revisiting it). `COST_BUDGET_MODE` stays `observe` (#97; `docs/configuration.md` says why: unpriced models fail closed, prices are unverified, there is no measured `would_block` rate, and the per-run ceiling does not bound a multi-agent turn).
- **Documentation.** The roadmap is organised for a visitor (what is next, what each release shipped, what is not planned). The parity matrix opens with a summary by status and one row per number. New pages: `docs/checkpoints-and-resume.md`, `docs/replay-mode.md`, `docs/preview.md`. Guides say what is true now without plan codes, and several were corrected against the code (merchant limits are enforced, telemetry export tries gRPC first, a UI smoke subset of Playwright runs in CI, `/ready` is unauthenticated, benchmark usage is recorded in every mode, releasing counts twelve images and six gating jobs, the API reference follows the auth route matrix and documents the orchestration endpoints, `POST /api/chat` is not claimed to need a token, `docs/model-providers.md` says plainly that Anthropic is stub-tested only). `CONTRIBUTING.md` gains prerequisites, a keyless test section and a PR checklist; the quick start leads with the shortest path and `scripts/dev.ps1` takes the same flags. The in-chat approval card renders only in the requester's own thread, so `support` cannot decide from it by design; staff decide on `/runs` or the inbox (component docs, `docs/approvals.md` and a vitest case).

### Fixed

**Security and correctness**

- **`draft_seller_response` is scoped to the caller's own products (#314, both stacks).** A seller could draft a response to a review of any seller's product. It now answers the same `Review not found: <id>` for another seller's review as for a missing one; an admin may draft for any review and every other role keeps the role denial. No tool schema or prompt changed, so no fixture moved.
- **Return-label tokens are 128 bits, and unknown-token lookups are rate limited (both stacks).** New tokens are 32 hex characters; 12-character legacy tokens work for 30 days from the return, then 404. Misses spend a per-address budget and a spent budget answers 429 before the lookup. See `docs/security-guide.md`.
- **PII redaction mangled UUIDs and long ids (D2).** The card pattern matched slices of a UUID, so a tool received `[REDACTED-CARD]-[REDACTED-CARD]` as an id. Card-shaped numbers must now pass Luhn, and no match may start or end inside a longer token; Python and .NET.
- **.NET `ProcessRefund` refuses a denied return** (it only refused an already-refunded one) with Python's payload.
- **`/runs` offered Approve to admins only, though staff decide.** The button shows to any staff member who is not the requester; run visibility is unchanged (admins read every run, everyone else their own, pinned by tests on both stacks).
- **Staff could not decide a paused return from the Approvals page, and `support` had no page to decide from (#208).** The page now reads the unified inbox (`GET /api/approvals`), decides through `POST /api/approvals/{id}/decide` and is open to `support` and `admin`. Web only.

**Gated calls and approvals**

- **A gated call now creates an approval instead of an empty reply, and one that could never succeed is no longer parked.** MAF stops a run at `approval_mode="always_require"` before function middleware, so the five gated tools wrote no `tool_approval_requests` row, no inbox entry, and the specialist answered with empty text. The host now records each parked call (idempotent, as the requesting user, tool not executed) and replies with a fixed sentence naming the request; no model call. `shared/park_prechecks.py` runs the role guard and per-tool preconditions first (ownership, status, return window, stock; predicates shared with the tools in `shared/gate_rules.py`) and answers in words with no row, so a customer's backorder, or a cancel of a shipped order, opens no approval; a foreign or missing order gets one neutral sentence. .NET runs the same checks before it parks (`ParkPrechecks`, rules shared with the tools in `GateRules`; parity row 55) and now matches the registered snake_case tool names, which it did not before.
- **On .NET a gated-call refusal or pending notice reaches the user from the host, not the model.** The pipeline records the sentence, the model is told only that the call was handled, and the host appends it after the run (final chunk when streaming), as Python does. `config/policies/testdata/gate-sentences.v1.json` pins every user-facing gate sentence on both stacks.
- **The eval harness sees a parked call.** It reads the `function_approval_request` (`shared.agent_host.approval_requests`) as `hitl_requested` plus the call and its arguments, in-process and over `--target http`; the live cancel case expects the park.

**Tools and agents**

- **The MCP tools are twins of the native tools (#320, #303).** The first comparison found eight of eleven calls differing in shape, `get_trending_products` ranked by order lines over MCP and by units sold natively, and a different tool picked for "when is it back in stock" (tool-call accuracy 0.955 native, 0.864 MCP). Each of the ten MCP tools now has the native tool's name, description (word for word), parameter schema, query, ranking and result fields; the product agent's MCP path also keeps `get_warehouse_availability` and the memory tools locally. The MCP packages cannot import `shared`, so the code is mirrored and `tests/test_mcp_native_equivalence.py` fails on drift in description or schema; `mcp-parity` fails on a different result; parity is 11 of 11 equal. `get_product_details` over MCP no longer returns `in_stock`, `seller` or `total_stock` (the native tool never did).
- **`get_warehouse_availability` no longer claims to include restock schedules (#320).** The description steered the model to it for "when will the Sony be back in stock" instead of `get_restock_schedule`. It now says it reports current per-warehouse stock only; native, MCP twin and .NET tool carry the same text. Replay keys moved for 69 fixtures that list the tool: their recorded responses are kept and the tool text re-keyed offline, and the restock case was recorded live on gpt-4.1. The no-key demo's 38 affected fixtures were re-keyed the same way. MCP tool-call accuracy is 0.955, equal to native.
- **Replay fixtures re-recorded after four prompt rules.** An order card is built only from an order tool result, never from the recent-orders summary (the context provider on both stacks lists id, status, total and date only, and now says it is a summary); a question about the user's own loyalty tier goes to pricing-promotions; the pricing agent validates a few named coupon codes at most and never claims a coupon is "already used"; the product agent calls a lookup tool before answering "not found". 71 recordings replaced 72. The product-discovery baseline moves from 0.97 to 0.96 overall.
- **A handoff recording missed in any other process.** MAF lists a handoff agent's targets from a set, so the tool order, and the replay key, changed per process. The key now sorts `handoff_to_*` tools among their own slots; no other key moves.
- **Search and tool data fixes.** `search_products` found nothing for an exact price (stored NUMERIC 14.99 against a float encoded as 14.99000...02); price filters are now exact two-decimal `numeric` values (D1). `check_bundle_eligibility` never matched seeded bundles (the seed stores product names under `products` and `category` and `min_quantity`); it now matches names case-insensitively, accepts both spellings and requires the minimum quantity (D4). `validate_coupon` accepted a negative cart; it now returns a structured `invalid_cart_total` error before any lookup (D3). `analyze_sentiment` reported the static `products.rating` (4.4 against 3.22); it averages the reviews now. Full-text search ties are ordered by product id (Python, MCP product server and .NET), and the .NET topic-sentiment text was `title ?? ("" + body)` through a precedence slip, so a review with a title was matched on the title alone.
- **.NET context enricher sends the full order id** (`order_id=<full id>`, like the Python provider); it cut ids to 8 characters, which a model cannot pass to `get_order_details`.
- **Two parallel specialist calls no longer cut each other off.** The shared A2A transport was closed by whichever client finished first, so the other stream died with a `ReadError`; a multi-intent prompt ("return my jacket and find a warmer one") answered without the search half.
- **Group chat answers with the panelists' words, not a placeholder.** The moderator's default verdict was "Synthesized 2 perspective(s)..."; it now shows the question followed by each panelist's text (#210); tutorial 22 (.NET) matches. No model call, no fixture change.
- **Python workflow modes advertised `supports_checkpoints=false`** although both attach checkpoint storage; the flag now matches the .NET twin.
- **Admin dashboards showed 0 pending requests.** `/api/admin/usage` (both stacks) returns `overall.pending_requests` and the admin pages read it.
- **Seed and demo data.** Alice's most recent seeded order is `delivered`, so the demo return flow works for her (added after everything else, no random numbers drawn). The price-check and multi-intent demo recordings were made against a 60-point price window that a fresh seed never gives (59); both are re-recorded and a test pins the window.

**Evals**

- **Grounding scoring.** A user-typed amount or id is `user_supplied` (counted as verified, listed separately) and an exact sum or difference of two grounded amounts is `derived`; a model-invented figure still fails (`--rescore` relabels older records). Amounts from `get_loyalty_tier` and `get_active_deals` are no longer unverifiable and `$1,000` is read as 1000. For the `orchestrator` target, `score_from_report(..., ledger_complete=False)` leaves amount and tracking verdicts out, because its ledger is empty across the A2A hop; ids and cards still count and specialist suites are unchanged. Production grounding and replay fixtures are untouched.
- **A judge call the content filter refuses is `judge_unavailable`, not completeness 0.** The case is scored by the deterministic keyword checks (`completeness_source: fallback_keywords`, never passing what the keywords would not), counted and averaged apart from judge-scored cases; with nothing keyword-checkable it stays out of the mean. A case the cost budget stopped is `budget_exceeded`, not an empty answer, and is left out of every mean.
- **Harness fixes.** A role-guard denial is detected in every output shape (a one-item list, a JSON string, plain text). The in-process loop resets the per-run token ceiling per case (it summed a whole suite against one ceiling and refused 17 of 30 orchestrator cases). In-process multi-turn orchestrator cases write the `messages` table like `/api/chat`, so a specialist reached over A2A rehydrates earlier turns. Foundry evaluator inputs use the shapes the SDK documents (message lists, `{name, description, parameters}` definitions, `--tool-definitions auto`); a live check on 5 pricing records removed both parse warnings.
- **Live-dataset fixes.** The pricing loyalty case asks the model to look the tier up (the replay wording let it answer from its context), and "cancel my last order" keeps its no-tool, no-approval expectation with a sharper `why`.

**Web, scripts and infrastructure**

- **"New chat" right after closing the Compare dialog did nothing (#210);** a closing dialog's backdrop kept catching clicks for its 100 ms exit animation. A slow conversation load no longer overwrites a newer "New chat" choice. The Mode dropdown shows its labels in full, and the Compare dialog renders a mode's fenced card block as its card (#209).
- **`RETURN_LIFECYCLE_ENABLED` and `MERCHANT_OPS_SCHEDULE_ENABLED` now reach the containers.** `docker-compose.yml` passed neither to the orchestrator or the outbox worker. Found by `scripts/flag-smoke.sh`.
- **`compose-smoke.sh` and `e2e-both-stacks.sh` run on macOS bash 3.2** (both used `declare -A`), and `test_config_repo_root` no longer fails on macOS. `aspire.config.json` (written by the Aspire CLI) is git-ignored.
- **Compose smoke retries cold starts** (login and the products read up to three times, the keyless chat turn once). It targets a one-off `Boot (net-py)` failure that passed on re-run; no root cause could be reproduced from the job alone.
- **Azure first-run hardening (optional deployment only).** The Postgres SKU preflight read the wrong level of the `az postgres flexible-server list-skus` JSON, so it rejected every region and `scripts/azure-up.sh` could not get past `prereqs` (fixed against a live read-only listing, pinned by `tests/test_azure_checks.py`). Resource providers are registered in parallel and polled, `azd auth login` and the subscription are checked and pinned, the embedding model quota is checked as well as the chat model, `Microsoft.Network` and (for the gateway) `Microsoft.ApiManagement` are registered, purge-protected and API Management soft-deleted names are recognised, `azure-down` survives a failing `azd down`, the smoke test waits for revisions to become healthy, and `azd provision` replaces `azd up`. `docs/azure-deployment.md` opens with a first-deploy checklist. Nothing outside `infra/`, `scripts/azure*` and the docs changed.

### Database

Migrations 0015 to 0020 are new since 1.4.0 (the list is in [docs/database-schema.md](docs/database-schema.md)): 0015 `usage_logs.price_source` and `priced_at`;
0016 `customer_confirmations`; 0017 price-drop and back-in-stock events and the draft table for the ops-responder; 0018 order risk, damaged items and
monthly budgets; 0019 `awaiting_manager` PO review for merchant proposals; 0020 `usage_logs.would_block`.

### Known limits

- **The free-trial profile was deployed from a clean subscription with one command, and tested.** `scripts/azure-up.sh` needs no arguments: every value (environment name, region, profile, image tag, model, accounts) has a default and a flag or `ECA_*` override, it shows numbered progress, and it ends by printing the URL, the documented demo accounts and what to try. A clean run provisioned in 7m32s and printed the banner at 10m30s; the smoke test (real model answers with product cards, a return paused, approved and executed) and the database, chat-turn and direct bearer-token model checks passed, and the teardown was verified clean. The apps reach the model with their managed identity; no model key exists. Bugs the real runs found are fixed and listed in [azure-deployment-sequence.md](docs/azure-deployment-sequence.md): a mistyped Key Vault role id (now pinned by a test), secrets generated too late for azd, deployments that must be named after their models for the cost guard, a region picker defeated by `pipefail`, and a model check that needs a retry window on a new account. The `demo` and `reference` profiles, the gateway and the portal button remain verified offline only.
- **The Azure deployment has never run against a real subscription.** The Bicep, the azd hooks, the optional Foundry, gateway, per-app identity and reference-profile modules and the scripts build, lint and pass offline tests. The APIM tier that supports the token policies is unverified. The first real deploy is a separate step with its own close conditions (`docs/azure-deployment.md`); until it passes, #89, #91 and the deploy chapter of #100 stay open. The Postgres admin password remains, because Entra authentication for the database needs code on both stacks.
- **Azure managed identity (`AZURE_OPENAI_AUTH=identity`) was tested against a fake credential only.** The default stays `key`.
- **The Anthropic provider is stub-tested on both stacks and has never made a live call.** Azure-hosted Claude answers on the Anthropic Messages route and has no capability row; Gemini and OpenAI direct have no stored live results either (#300).
- **A real carrier feed is external.** Event autonomy and the return lifecycle are driven by the admin simulator only (#299, #95).
- **`orders.on_hold` blocks nothing.** The risk review records and reverses the flag and shows it on the decisions page, but no checkout, fulfilment or shipping code reads it yet.
- **Every autonomy cap, signal and threshold, and the merchant PO-value threshold, are placeholders.** Event autonomy, merchant proposals, the return lifecycle and the merchant-ops agent are Python only; all four are off by default.
- **.NET does not port the turn-level cost ceiling** (parity row 58), and answers 404 for merchant proposals and 422 for autonomy approvals.
- **The .NET Aspire AppHost has not been booted,** and `Azure.AI.OpenAI` stays on a prerelease (2.9.0-beta.1) on the .NET stack.
- **Live eval results are k=1 reference runs on one model and one judge.** They are stored for six suites; the merchant-ops and approvals live runs are not stored as baselines, and the judge-calibration labels are drafted and unreviewed.
- **The Codespaces key handling and the `.devcontainer` build job have not been tried in a real Codespace.**
- **The static preview replays recorded answers only;** anything unrecorded says so.

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
  it in place, see [Troubleshooting](docs/troubleshooting.md#productssearch_vector-does-not-exist).
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

[Unreleased]: https://github.com/nitin27may/e-commerce-agents/compare/v1.4.0...HEAD
[1.4.0]: https://github.com/nitin27may/e-commerce-agents/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/nitin27may/e-commerce-agents/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/nitin27may/e-commerce-agents/compare/v1.0.0...v1.2.0
[1.1.0]: https://github.com/nitin27may/e-commerce-agents/releases
[1.0.0]: https://github.com/nitin27may/e-commerce-agents/releases/tag/v1.0.0
