# Handover

Working notes for resuming this project in a fresh session (local or cloud). Read this first, then
`CLAUDE.md`, then `.claude/plans/remaining-work.md` (the one plan; its "Consolidated plan, 2026-11-20"
and "Progress" blocks are the live part). **Update the Progress log at the bottom of this file in the
same PR as any work you land.** Facts here were true on the date shown; verify a name or number against
the code before relying on it.

Last updated: 2026-11-23, main at `4ca4601`, release `v1.4.0` published (tagged at `9d83d45`).

## 1. State in one screen

- Repo `nitin27may/e-commerce-agents`, default branch `main`, squash-merge only, history is the record.
- **v1.4.0 is published** (maintainer approved the `release` environment on 2026-11-23; announcement is
  Discussion #204). The next release is v1.5.0 (plan NA-11); its approval click stays the maintainer's: do not
  approve `release.yml` through the API unless they tell you to.
- About 100 PRs of the 2026-11 effort are merged. Since v1.4.0 main has gained: run profiles and `doctor`, the pricing
  file and token budget, gateway headers, Foundry evaluators and community results, the `http` eval target and the
  keyless stack-parity gate, live datasets with multi-turn cases, the rebuilt manual live job, durable parking of gated
  tool calls (#258), Aspire 17, and the docs audit fixes. Open PRs change often (`gh pr list`); at the time of writing
  they were Azure preparation, further live suites, judge calibration and capability results, and doc passes.
- Remote branches: `main` plus whatever is open. Delete a branch after its PR merges (see section 6).
- Two Claude sessions work in this repo: this "queue" session (features, CI, Azure, evals, cleanups) and
  `e-commerce-agents-ac` (landing pages, docs wording, the release). Coordinate with `SendMessage` to
  `uds:/tmp/cc-socks/58881.sock` (check `ListAgents` for the current address). It owns README, docs
  landing pages, `docs/roadmap.md`, `docs/parity-matrix.md` wording and the release procedure
  (`docs/releasing.md`); ask before restructuring those. Its handover is folded into
  section 9 below (the doc/code mismatches it suspects but has not verified, the wording rules, and the
  checks to run after any docs change). Read it before touching docs.

## 2. What exists now (high level)

- 6 specialist agents on MAF Python, a .NET port at feature parity (decision D-3: .NET stays at parity),
  A2A 1.0 on both stacks, approvals with policy/SLA/escalation, merchant proposals, event autonomy,
  return lifecycle (Python), 5 orchestration modes, hybrid search (pgvector + full text).
- Built in the 2026-11 effort: history compaction (opt-in, both stacks); memory/profile output
  sanitising; group-chat prompts in YAML; signing-key rotation and a route/credential matrix; `--no-key`
  demo mode; tutorial notebooks 01 to 04; typed web API and external auth/cart stores; UI accessibility
  and palette pass; Aspire AppHost (13.6.5); secret scan, MAF canary, badge check and mixed-stack boots in
  CI; `db-init` image and `seed --if-empty`; optional empty `LLM_TEMPERATURE`, `DB_POOL_*`, .NET `sslmode`;
  `AZURE_OPENAI_AUTH=key|identity`; Bicep infra, Container Apps modules, azd scripts, smoke test and a
  gated deploy workflow; eval scorecard, model providers guide, Durable Task spike (recommendation: keep
  the Postgres checkpoint + outbox design); .NET approval-timeout worker (parity row 36 is Full).
- Security fix landed: the return-label route matched tokens with `LIKE` (a `%` returned any customer's
  label). Fixed on both stacks; tokens are still 48 bits and unknown-token 404s are not rate limited.

## 3. Not done, and what each needs

| Item | Needs | Notes |
|---|---|---|
| First real Azure deploy | Maintainer decision D-1 (spend), fresh `az login`, a published image tag, public GHCR packages | Everything under `infra/`, `azure.yaml`, `scripts/azure*` is offline-verified only. Runbook: `docs/azure-deployment.md`; first-deploy commands are in PR #172's description. |
| Live-model eval run | A working local Docker + a model key (the Azure OpenAI key in the owner's `.env`) | Run `scripts/live-evals.sh` (procedure in section 5). First live run was 2026-11-23; the suites have grown since, so run again before quoting numbers. Never run in CI automatically. |
| R0 recording session (about $1 to $3) | A key; run by a person | Records demo fixtures into `agents/python/demo/fixtures/replay/`, tutorial fixtures for chapters 04, 05, 07, 12 to 14, 16, the demo GIF. Then add `COPY demo/` to `agents/python/Dockerfile`. |
| Keyed features: D-5b customer confirm, B-16 `GroupChatBuilder`, M-2 merchant agent, V-1a `ops-responder`, E-4 red-team judge, E-6, E-7 MCP-path gate, E-8 .NET eval datasets, typed `ProductFilters` DSL, B2 dual-stack parity in CI | A key; record all in one sitting so nothing is recorded twice | See plan Part IV workstreams B, D, E. |
| Foundry, AI gateway, realistic evals, run profiles | M0 prerequisites at a machine; one R1 key sitting (M3) | Planned as milestones M0 to M8 in the plan's "Execution plan" block of 2026-11-21 (supersedes Part I C to E). D-1 is approved: Azure spend as needed. M1 and M2 can start now; M0, M3, M4 need a human at a machine. Rule kept: Foundry pieces optional, the core eval path imports no Azure package. |
| README screenshots and GIF (issue #182) | Docker healthy, then the R0 recordings | The docs lane's caveats about `--no-key` recordings and `COPY demo/` come out once those ship. Also click through `demo-guide` scenarios 9 to 11, which nobody has run. |
| Dependency review workflow | Maintainer enables Dependabot alerts + dependency graph in repo settings | The job returned 403 without them; it was removed from PR #166. |
| LangGraph backend (LG1 to LG3) | Parked by maintainer decision | Plan recommends one LangGraph specialist behind A2A instead of a third stack. |

Smaller known follow-ups: FTS arm `ts_rank` ties have no explicit tie-break (Python and .NET); other
`LIKE`/`ILIKE` uses in .NET were not swept; usage route lacks a `pending_requests` field (the web page
hard-codes 0); about 66 raw palette classes remain (mostly `products/[id]`, `lib/agents.ts`, `cart`,
`checkout`); `test_config_repo_root` fails on macOS only (`/System/Volumes/Data` path); .NET
`sslmode=require` certificate validation on Npgsql 10 was not confirmed; `AddUvicornApp`/`WithPnpm`
behaviour in the AppHost was never booted.

## 4. Decisions on record

- D-3: .NET stays at parity. D-2 and D-1 are open (objective; Azure spend ceiling).
- Azure chat model default is **gpt-5.1** (gpt-4.1 is closed to new customers; gpt-5.x rejects
  `temperature`, so `LLM_TEMPERATURE=` empty omits it). gpt-4.1 stays an override.
- `Azure.AI.OpenAI` is pinned to the prerelease 2.9.0-beta.1 (maintainer approved; no stable release after
  2.1.0; the old pin threw `MissingMethodException` on native chat calls with a max-token count).
- Aspire: the AppHost packages are 17.0.0 (`Directory.Packages.props`; the AppHost uses `Microsoft.NET.Sdk` and `AddDotnetProject`). Only the dashboard container image is still the 13.6.5 tag in the compose files.
- Redis on Azure: a container app for the demo profile (Azure Cache for Redis is closed to new customers).
- Plan correction: a return over $500 routes to `manager`, which `support` cannot decide.

## 5. Procedures that took time to learn

**Replay gate** (keyless, the thing CI runs): `scripts/replay-evals.sh --setup` on a fresh database. Main
is clean (zero misses). A miss means a prompt, tool schema or tool output changed: re-record or
`uv run python -m evals.rehash_fixtures` (only if the response is untouched). Check
`uv run python -m evals.fixture_drift` first.

**Live eval against a real model** (costs a few dollars; the owner approves each run):
- The script is `scripts/live-evals.sh` (`--check-env` validates the configuration and calls nothing; `--setup` migrates
  and seeds a fresh database first). It runs every `evals/datasets/live/*.live.json` through the in-process target against
  a Postgres service container in CI (`full` job in `.github/workflows/evals.yml`, `workflow_dispatch` only; inputs and
  secrets in `agents/python/evals/README.md#live-runs-in-ci`). Print the cost first with `evals.run_evals --estimate`.
- **Isolated local run, the pattern that keeps the owner's data safe:**
  1. Use a clean worktree of `main`; `cd agents/python && uv sync --all-packages --extra dev`.
  2. **Do not `source` the repo's `.env`.** Its `DATABASE_URL` may point at a different project's database and the seeder
     truncates. Pick only the model variables (`AZURE_OPENAI_*` or the provider's key) and set `DATABASE_URL`,
     `REDIS_URL` and `LLM_PROVIDER` explicitly to your own project's database.
  3. `docker compose -p eca-live up -d db redis` (own project name), then `scripts.migrate` and `scripts.seed`; the seeder
     does the embeddings work, so there is no separate `generate_embeddings` step.
  4. Run `scripts/live-evals.sh` (it boots the five specialists for the orchestrator suite). Live cases write to the
     database (`initiate_return`, a parked `cancel_order`), so the database is throwaway: reseed between runs.
  5. Kill only the PIDs you started; `docker compose -p eca-live down -v`.
- There is no `--baseline` for a live run (baselines are keyed by setup and none exists for a live model); the threshold
  is the gate. Do not pass `--update-baseline`. Keep run records out of the repo unless a plan item says otherwise.

**Shared resources on one machine:** specialists bind 8081 to 8085 and Postgres 5432. Serialise anything
that uses them with `flock /tmp/eca-evals.lock`. Never use the default compose project (a volume
`e-commerce-agents_pgdata` holds the owner's real dev data); give every run its own `-p <name>` and
`down -v` it. `dotnet` under `flock` leaves MSBuild workers holding the lock: pass
`--disable-build-servers -p:UseSharedCompilation=false -nodeReuse:false` (or skip `flock`; Testcontainers
tests use random ports). Do not `pkill` by name; kill exact PIDs. `scripts/replay-evals.sh` now cleans up
its specialists correctly on macOS (#160, #189).

**Docker Desktop can wedge** (container API hangs while `docker info` still answers; Testcontainers then
fails every test in milliseconds). `docker info --format '{{.ServerVersion}}'` empty means the engine is
down. Only the owner can restart it; CI on GitHub is the fallback for .NET and Postgres-backed tests.

## 6. How work flows

- Branch from `origin/main`, small PRs, squash merge on green CI. A `merge-when-green` loop that waits
  until no check is `pending`, refuses on any `fail`, then `gh pr merge --squash` is enough. A PR with
  merge conflicts runs no CI at all (it shows no checks), so check `mergeable` when a PR looks stuck.
- Every PR appends to `CHANGELOG.md` `[Unreleased]` and often adds a parity row, so **conflicts there are
  routine**: keep both sides, keep rows in numeric order. Re-run the touched tests after resolving.
- Version bumps and the release belong to `docs/releasing.md` and the other session. Do not tag.
- After a PR merges, delete its remote branch only if its merged PR's head SHA equals the branch tip, and
  there is no open PR from it. Never delete another session's worktree or branch.
- A local safety hook blocks a command line that contains both "force" and "main" (even for a feature
  branch); run `git push --force-with-lease origin <branch>` as its own command. Use `--force-with-lease`
  only on your own PR branch.
- Do not put secrets in commits, settings or PR text. The owner's `.env` holds real keys; read single
  variables, never print them.
- CI facts: the .NET job and Python job are the slow ones (several minutes). The mixed-stack smoke jobs
  (`py-net`, `net-py`, and `a2a` + token variants) caught a real crash once (blank `DB_POOL_*` reaching Python).
  A flaky .NET test was fixed by putting every `ScriptedSpecialist` test class in
  `ProcessEnvironmentCollection`; new classes that start one belong there too.

## 7. Where things live

- Plan: `.claude/plans/remaining-work.md` (Consolidated plan 2026-11-20 with lanes, order, the Azure
  deployment design and failure catalogue; Progress block; Part IV is the build log).
- Docs index: `docs/README.md`. Azure: `docs/azure-deployment.md`, `docs/troubleshooting.md`. Config:
  `docs/configuration.md`. Providers: `docs/model-providers.md`. Evals: `docs/agent-quality.md`,
  `docs/eval-scorecard.md`. Parity: `docs/parity-matrix.md` (rows 1 to 54, number 45 unused; the Summary is checked against the table by `build_docs_site.py --check`). Auth routes:
  `docs/auth-route-matrix.md`. Approvals: `docs/approvals.md`.
- Infra: `infra/` (Bicep, profiles, `ci/*.bicepparam`), `azure.yaml`, `scripts/azure/`, `scripts/azure-up.sh`,
  `scripts/azure-down.sh`. Offline checks: `.github/workflows/infra-offline.yml`; deploy workflow
  `.github/workflows/azure.yml` is dispatch-only with an `azure-deploy` environment.

## 8. Suggested next steps, in order

The order of record is the plan's "Non-Azure plan" block. In short, as of 2026-11-23:
1. Land the open eval, documentation and Azure-preparation PRs (`gh pr list`), then one clean `scripts/live-evals.sh`
   run on the grown datasets (NA-1) and quote its numbers with the setup and date.
2. Judge calibration, provider capability results and reference baselines (NA-3, NA-4, NA-6), then the single R1
   recording sitting that batches every keyed fixture (NA-7).
3. v1.5.0 (NA-11) after R1: changelog as release notes, docs and scorecard in sync.
4. The documentation audit's final pass (NA-12): the `CHANGELOG.md [Unreleased]` regrouping once every PR has merged.
5. The first real Azure deployment last (AZ-LAST), on the existing environment, after the items above.

## 9. Documentation lane (folded in from the docs session)

Everything here is on `main` or GitHub. Done on 2026-11-21 by the docs session: GitHub About description
and topics rewritten (application first, course second); README, `docs/index.md`, architecture, demo
guide and agent flows lead with the complete application and the five orchestration modes (#179); all 34
tutorial chapter READMEs rewritten for learners (#190); chapter pointers by symbol instead of line
number, enforced by `scripts/check_tutorial_readmes.py`, plus a status table and a refreshed chapter
template (#196); roadmap and parity matrix reorganised for visitors, ten guides cleaned of plan codes,
new `docs/checkpoints-and-resume.md` and `docs/replay-mode.md` (#195); release 1.4.0 with a "Known limits"
section in the changelog (#193); third pass over quick start, troubleshooting, the 14 concept pages, configuration, database schema, API reference, audit matrix, benchmark and releasing (#198); Discussion "Which orchestration pattern should I use?" is #188.

**Open, in order:**
1. Done 2026-11-23: release 1.4.0 approved and published.
2. Done 2026-11-23: announcement is Discussion #204. Pin it and #188 from the Discussions UI.
3. Screenshots and a README GIF (#182, also #87): Mode dropdown, live graph, Compare dialog, the
   approvals flow, one Aspire trace. Needs a running stack and a key. The README hero is
   `docs/images/flow-product-search.png` until then.
4. Click through `demo-guide` scenarios 9 to 11 (switch mode, compare, approve a paused return). They were
   written from the code, not run in a browser. Staff decide from **Runs**; **Admin, Approvals** is
   admin-only.
5. Ship the `--no-key` recordings. `agents/python/demo/fixtures/replay/` is empty, so the no-key stack
   answers every chat prompt with "not recorded". README, quick start, demo guide, `docs/replay-mode.md`
   and the 1.4.0 changelog all say so; remove those caveats when it ships.

**Suspected mismatches, outcomes as of 2026-11-23:** parity rows 29 and 33 carried a priority although Full (priority
cleared in the docs audit fixes, PR 265); the two unreferenced guest screenshots (one 12 MB) were deleted in the same PR;
the guardrails snippet in `docs/concepts/10-guardrails.md` now lists the cost-budget, moderation and grounding-ledger
layers; `docs/reported-vs-actual.md` no longer ties one claim to two test counts; the `build-images.yml` comment now says
`publish-main` needs six test jobs, which is what `tests.yml` lists; the API reference's "about 70 routes" is right
(69 `@router` decorators plus one in `main.py`). **Still true:** Python `ReturnReplaceMode.capabilities` has
`supports_checkpoints=False` although the mode checkpoints every run (that one is code, not docs); the API reference's
overview diagram may still imply every non-auth route needs a token (not re-checked).

**Limits of the new tutorial checks:** the symbol check passes a path if any backticked identifier in the
same paragraph or list item occurs in the file (no one-to-one pairing); `web/src/app/(app)/runs/page.tsx`
is not checked because the path pattern rejects parentheses.

**Wording rules used for reader-facing pages:** say what is true now (history goes in `CHANGELOG.md`); no
plan or lane codes and no pointers into `.claude/plans/`; open with what the page is and who it is for;
payoff before caveats, caveats kept; no fact changed without checking the code; second person, present
tense, no sentence much over 35 words. "Course" for the chapters, "complete application" for the app;
"tutorial" only as a search topic.

**Checks to run after any docs change:**

```bash
python3 scripts/build_docs_site.py --check
python3 scripts/check_tutorial_readmes.py --check
python3 scripts/check_tutorial_coverage.py --check
python3 scripts/check_tutorial_nav.py --check
python3 scripts/run_tutorials_keyless.py            # after uv sync --project tutorials
cd agents/python && uv run pytest tests/test_check_tutorial_readmes.py tests/test_check_tutorial_coverage.py -q
```

Time sinks: `uv lock` with a different local uv version rewrites unrelated markers in
`agents/python/uv.lock` (for a version bump, edit the one `version =` line); the parity matrix and
`CHANGELOG.md` are touched by every lane, so merge `main` just before pushing.

## Progress log

Append newest first. One line per landed change: date, PR, what, and anything left unverified.

- 2026-11-24: course chapters 33 (prompt injection and red team), 34 (structured output), 35 (memory) and 36 (model providers), Python only, keyless; the course is now 38 chapters. #100 stays open: the Azure deployment, durable workflows, Foundry evals and AG-UI chapters are not written. Unverified: OpenAI direct (api.openai.com) and Anthropic were never called, and chapter 36 says so; the chapters' four recordings were made on one Azure deployment of `gpt-4.1`.
- 2026-11-23 (docs audit, group two): approvals, tutorials 06 and 17 and concept 11 describe the host-level park of gated tool calls; eval dataset counts dated; CLAUDE.md gains the parked-call and live-dataset traps; this handover and the plan refreshed. Left for the final pass: `CHANGELOG.md [Unreleased]` regrouping (CL-1 to CL-8) and anything that depends on the still-open eval PRs.
- 2026-11-23: plan gains a "Status, 2026-11-23" block (built since the 21st, what the first live eval found, remaining work by owner). Landed: #213 Foundry/APIM/identities, #214 gateway headers, #216 Foundry evaluators + community results, #220 orchestrator groundedness scoring, #222 live-capable datasets, #223 reference profile + .NET on Azure + wired identities, #236 .NET image smoke test now has a pgvector DB, Dependabot #224/#225/#227-#230. In flight: #215 (http target + stack-parity job, row 53); held: #226, #231-#233, #235 (Aspire 17, .NET tests red), #234 (net-py boot re-run). Unverified: no .NET image has run on Azure; the new smoke step's first real result is on the next PR touching images. A watcher once merged #223 before its last fix was on the PR head; check the PR head SHA before relying on a watcher.
- 2026-11-23 (docs session): #210, first item only: the Mode dropdown popup is as wide as its longest label. Verified in a browser. The other three items in #210 are open (group chat's placeholder answer, Alice's latest order being `shipped`, "New chat" after Compare); the first two touch backend text and seed data, so check replay fixtures before changing them.
- 2026-11-23 (docs session): #209 fixed: Compare result cards use the chat renderer (`RichMessage`), so fenced card blocks show as cards. Verified in a browser against the v1.4.0 backend with `tool` and `handoff`. `docs/images/mode-compare.jpg` still shows the old dialog; retake it with the #182 visuals.
- 2026-11-23 (docs session): #208 fixed in the web app: `/admin/approvals` reads `GET /api/approvals`, decides through `/decide`, and is open to `support` and `admin` (`staffOnly` nav item). Verified in a browser against the running v1.4.0 backend: a support-routed return appeared in support's inbox and Approve executed it. The released 1.4.0 frontend image does not have this; it ships with the next release. Not changed: the Runs page still gates Approve on admin, and `/api/runs` shows support only its own runs.
- 2026-11-23 (docs session): Docker Desktop was wedged because its disk was full; restarted it and pruned build cache (16.9 GB). Ran the v1.4.0 demo images and clicked through demo-guide scenarios 9 to 11. 9 and 10 work as written. 11 was wrong and is rewritten: Alice's latest order is `shipped` so her return stops at eligibility (use `tour@example.com`); an admin approves from **Runs**; the `support` role has no page to decide from and **Admin, Approvals** does not list workflow approvals (#208). Also filed #209 (Compare shows raw JSON) and #210 (polish). Screenshots added: `mode-compare.jpg`, `return-awaiting-approval.jpg`, `runs-approve.jpg`. Still open under #182: the README GIF, the live graph mid-run, an Aspire trace. The browser automation stopped delivering clicks after a few sign-in changes, so approve was triggered by script, not by a mouse click.
- 2026-11-23 (docs session): v1.4.0 is published. The maintainer approved the `release` environment; the release run finished green, the GitHub release exists, and `orchestrator`, `frontend` and `db-init` answer an anonymous registry request for `:v1.4.0` and `:latest` with both `amd64` and `arm64` (checked through the registry API, not `docker pull`, because the local Docker daemon is still wedged; the other nine packages were not checked). Announcement posted as Discussion #204; pinning it and #188 is a manual click in the UI. Still open: #182 screenshots and GIF, demo-guide scenarios 9 to 11, the `--no-key` recordings.
- 2026-11-21: plan block rewritten as an execution plan, milestones M0 to M8 with a verified current-state table; D-1 approved (Azure spend as needed, nothing parked); D-9 opened (MCP ingress for Foundry-hosted agents: `apps.bicep` gives MCP internal ingress today). Plan only.
- 2026-11-21: maintainer decisions recorded in the plan: D-1 not yet (tier-2 infra parked; keyed evals on the existing Azure OpenAI key still allowed), all providers supported with Azure gpt-5.1 + OpenAI direct as reference baselines, D-6 all six agents on Foundry (staged route), D-7 one fixed judge.
- 2026-11-21: EV-12 first half on `claude/new-session-ytrmq5`: prices moved out of `shared/cost.py` and `CostEstimator.cs` into `config/pricing/models.yaml` (D-8 = a), unknown model costs null, enforce fails closed when unpriced, CLAUDE.md "Model pricing". Python and .NET cost tests pass; DB-backed tests on both stacks need Docker (not available in the cloud container) and were not run. Two `test_azure_identity_auth.py` failures predate the change.
- 2026-11-21: same block reframed around the goal "any provider, a trustworthy eval for it; Azure opt-in" (tiers 0-2; EV-10 to EV-14, RP-6 added; EV-6 provider-neutral). Found: `shared/cost.py` prices unknown models as gpt-4.1 (EV-12).
- 2026-11-21: plan block "Azure track, evals and run profiles" added (workstreams EV, AZ, RP; decisions D-6 Foundry hosting scope and D-7 judge model opened). Plan only, no code.
- 2026-11-21 (docs session): #198 merged, third docs pass (quick start, concepts, configuration, schema, API reference, benchmark, audit matrix, releasing); it fixed four of the previously suspected mismatches and corrected `docs/concepts/14` (idempotency, retry and rate limiting do exist). #195 was merged before its CI finished (an `--auto` merge fired at once); every workflow on that commit later passed on `main`. The 1.4.0 release run is still waiting on the maintainer's approval; no announcement posted yet. Not started: #182 screenshots and GIF.
- 2026-11-21 (docs session): issue #183 closed by #195; a third docs pass is starting on branch `docs/third-pass` (concept pages, configuration, database schema, API reference, quick start, troubleshooting, releasing, benchmark, reported-vs-actual, audit matrix, plus verifying the suspected mismatches in section 9).
- 2026-11-21: #194 .NET approval-timeout worker (row 36 Full); v1.4.0 tagged; handover document added.
- 2026-11-21: #158 to #193: see the plan's Progress block for the list.
