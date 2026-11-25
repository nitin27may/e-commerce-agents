# Handover

Working notes for resuming this project in a fresh session (local or cloud). Read this first, then
`CLAUDE.md`, then `.claude/plans/remaining-work.md` (the one plan; its "Live status and order" block is the live part).
**Update the Progress log at the bottom of this file in the same PR as any work you land.** Facts here were true on
the date shown; verify a name or number against the code before relying on it.

Last updated: 2026-11-24, `main` after #328, release `v1.4.0` published, v1.5.0 staged (not tagged).

## 1. State in one screen

- Repo `nitin27may/e-commerce-agents`, default branch `main`, squash-merge only, history is the record.
- **v1.4.0 is published** (announcement is Discussion #204). **v1.5.0 is staged**: the `CHANGELOG.md` `[Unreleased]` section is
  written as its release notes (Added, Changed, Fixed, Database, Known limits). Cutting it follows `docs/releasing.md`
  (`scripts/bump_version.py`, then the `release` environment approval, which stays the maintainer's click: do not approve
  `release.yml` through the API unless they say so).
- Everything that needs no Azure subscription is built and merged. What is left, and why, is the table in the plan's live
  block: the Azure run is done (PR #263 merged; #89 and #91 closed, remainders in #335) and only the deploy chapter of #100 is left; #97, #299 and #303 have remainders; #300 needs keys
  that do not exist here; #101 is parked; #318 is a human task (narrated video, social preview upload); #103 is the tracker
  and closes last. PR #263 (the keyless one-command Azure deployment) is merged.
- The .NET 11 move is PR #331 (`chore/dotnet-11`, `net11.0`, SDK 11.0.100), now that the Azure run is done. Rollback is a single revert.
- Open PRs change often: `gh pr list`. Remote branches: `main` plus whatever is open. Delete a branch after its PR merges
  (section 6).
- Two Claude sessions have worked in this repo: a "queue" session (features, CI, Azure, evals, cleanups) and a docs session
  (landing pages, docs wording, the release). Check with the maintainer before restructuring README, docs landing pages,
  `docs/roadmap.md`, `docs/parity-matrix.md` wording or the release procedure.

## 2. What exists now (high level)

- A Python orchestrator and five specialists (ports 8081 to 8085), plus an opt-in sixth Python-only specialist, `merchant-ops`
  (port 8086, `MERCHANT_OPS_AGENT_ENABLED`, parity row 57). A .NET port at feature parity except the rows the matrix marks
  Python only (decision D-3: .NET stays at parity). A2A 1.0 on both stacks, one approvals inbox with policy, SLA and
  escalation, customer confirmation before a gated call parks (both stacks, row 56), merchant proposals with a PO-value
  manager tier (Python), event autonomy with five event types and monthly budgets (Python, off by default), the return
  lifecycle (Python), five orchestration modes, hybrid search (pgvector plus full text).
- 38 tutorial chapters (the last four, 33 to 36, are Python only and keyless), a key-free demo (`--no-key`) with recorded
  answers, a static hosted preview under `/preview` of the docs site (`scripts/build_preview_data.py`, `pnpm build:preview`),
  a README GIF recorded keyless (`scripts/record-demo.sh`).
- Evals: replay gate (keyless, CI), live datasets with resolved facts and multi-turn cases, the `http` target, a keyless
  stack-parity gate, MCP-versus-native gate (`scripts/mcp-compare.sh`), per-mode orchestrator scoring, indirect-injection and
  red-team judge suites, six stored live baselines, judge calibration (labels drafted, unreviewed), provider capability
  results, optional Foundry evaluators, community results.
- Operations: run profiles and `scripts.doctor`, one pricing file (no price in code), per-run and turn-level cost ceilings,
  gateway attribution headers, Bicep for Azure (demo and reference profiles, optional Foundry, gateway and per-app identities;
  never deployed), `scripts/flag-smoke.sh` for the off-by-default flags.
- Database: `init.sql` baseline (34 tables) plus migrations 0002 to 0020 (13 more tables, 47 in all). The list with one-line
  meanings is in `CLAUDE.md` (Database).

## 3. Not done, and what each needs

| Item | Needs | Notes |
|---|---|---|
| First real Azure deploy (closes #89, #91, deploy chapter of #100) | Maintainer go-ahead for spend (free trial, 10 USD cap), a fresh `az login`, public GHCR packages, PR #263 merged | Order and the four close conditions are in the plan's live block. Runbook: `docs/azure-deployment.md`. Everything under `infra/`, `azure.yaml`, `scripts/azure*` is offline-verified only |
| Foundry features that need an Azure credential | The Azure step first | `--log-to-project`, the Foundry safety evaluator, hosted agents (ADR 0006, Proposed) |
| .NET 11 move | Nothing external | Target framework, Dockerfiles, CI SDK, Aspire AppHost, then every .NET test and both mixed-stack boots |
| .NET live baselines (rest of #97) | A key, about 1.50 USD of tokens | Record, then gate on baseline regression |
| Anthropic and Gemini scorecard rows (#300) | A key for each | Anthropic stays stub-tested by decision; Azure-hosted Claude answers on the Anthropic Messages route |
| Dependency review workflow (#303) | The owner enables Dependabot alerts and the dependency graph in repository settings | The job returned 403 without them |
| Judge-calibration labels | The maintainer reviews `agents/python/evals/judge_calibration/labels.jsonl`, then `uv run python -m evals.judge_calibration.run --rescore` | No model call |
| Real carrier feed (#299, #95) | An external system | The admin simulator is the only producer of carrier and delay events |
| Fulfilment that honours `orders.on_hold` | A decision | The risk review sets the flag; nothing reads it, so a hold blocks nothing |
| Narrated walkthrough video, social preview upload (#318) | A person | |
| LangGraph backend (LG1 to LG3), #101 | Parked by maintainer decision | One LangGraph specialist behind A2A would beat a third stack |

Smaller known follow-ups: other `LIKE`/`ILIKE` uses in .NET were not swept; about 66 raw palette classes remain (mostly
`products/[id]`, `lib/agents.ts`, `cart`, `checkout`); .NET `sslmode=require` certificate validation on Npgsql 10 was not
confirmed; `AddUvicornApp`/`WithPnpm` behaviour in the AppHost was never booted; Codespaces key handling was never tried in a
real Codespace; every autonomy cap, signal and threshold and the merchant PO-value threshold are placeholders.

## 4. Decisions on record

- D-3: .NET stays at parity. D-1: Azure spend is approved but bounded (free trial, 10 USD cap) and the deployment is last. D-2:
  the objective is a worked reference, not a product.
- Anthropic stays stub-tested only, by maintainer decision. `azure_content_safety` stays unimplemented and `COST_BUDGET_MODE` stays
  `observe` (both recorded in `docs/agent-quality.md` and `docs/configuration.md`).
- `Azure.AI.OpenAI` is pinned to the prerelease 2.9.0-beta.1 (no stable release after 2.1.0; the old pin threw
  `MissingMethodException` on native chat calls with a max-token count).
- Aspire: AppHost packages 17.0.0 (needs .NET 10 or later); the dashboard container image stays on 13.6.5 because no 17.x tag exists on MCR.
- Redis on Azure: a container app for the demo profile (Azure Cache for Redis is closed to new customers).
- A return over $500 routes to `manager`, which `support` cannot decide; the deploy smoke test picks the decider from the approval's
  `assigned_role`.
- Merchant-ops is opt-in and Python only. Event autonomy, merchant proposals and the return lifecycle are Python only and off by
  default (parity rows 39, 40, 41, 57).

## 5. Procedures that took time to learn

**Replay gate** (keyless, the thing CI runs): `scripts/replay-evals.sh --setup` on a fresh database. A miss means a prompt, tool
schema or tool output changed: re-record, or `uv run python -m evals.rehash_fixtures` (only if the response is untouched). Run
`uv run python -m evals.fixture_drift` first; exit 0 does not prove the fixtures match (see `CLAUDE.md`, Testing & Evals).

**Live eval against a real model** (costs a few dollars; the owner approves each run):
- The script is `scripts/live-evals.sh` (`--check-env` validates the configuration and calls nothing; `--setup` migrates and seeds a
  fresh database first). It runs every `evals/datasets/live/*.live.json` through the in-process target against a Postgres service
  container in CI (`full` job in `.github/workflows/evals.yml`, `workflow_dispatch` only). Print the cost first with
  `evals.run_evals --estimate`.
- **Isolated local run, the pattern that keeps the owner's data safe:**
  1. Use a clean worktree of `main`; `cd agents/python && uv sync --all-packages --extra dev`.
  2. **Do not `source` the repo's `.env`.** Its `DATABASE_URL` may point at a different project's database and the seeder truncates.
     Pick only the model variables (`AZURE_OPENAI_*` or the provider's key) and set `DATABASE_URL`, `REDIS_URL` and `LLM_PROVIDER`
     explicitly to your own project's database.
  3. `docker compose -p eca-live up -d db redis` (own project name), then `scripts.migrate` and `scripts.seed`.
  4. Run `scripts/live-evals.sh`. Live cases write to the database (`initiate_return`, a parked `cancel_order`), so the database is
     throwaway: reseed between runs.
  5. Kill only the PIDs you started; `docker compose -p eca-live down -v`.
- There is no `--baseline` for a live run; the threshold is the gate. Do not pass `--update-baseline`. Keep run records out of the repo
  unless a plan item says otherwise.

**The merge helper.** `scripts/merge-when-green.sh <pr>` is the only way a PR is landed by a session. It records the head SHA when it
starts, then squash-merges only if the head SHA has not moved, at least 20 checks are complete and none failed, and the PR is
mergeable, on two polls 45 seconds apart. It pins the merge with `--match-head-commit`, never uses `--admin`, and exits non-zero with
a reason otherwise. A non-zero exit is information: read the reason, do not loop around it. A watcher once merged a PR before its last
fix was on the head; this is why the SHA is pinned. A PR with merge conflicts runs no CI at all (it shows no checks), so check
`mergeable` when a PR looks stuck.

**Registry 503s in CI.** A boot or image-smoke job that fails while pulling a base image or a package (a 503 from a registry or from GHCR)
is an infrastructure failure, not a code failure: confirm the failing step is the pull, then `gh run rerun <id> --failed`. Do not change
code to chase it, and do not count a green rerun as proof of anything beyond "the registry came back". The same applies to the one-off
`Boot (net-py)` failure that no root cause could be found for (compose smoke now retries cold starts).

**Secret scanning and CodeQL.** `gitleaks` scans every PR (`secret-scan.yml`, rules in `.gitleaks.toml`). A test that carries a fake
token or an id-shaped vector trips it: add a narrow allowlist entry for that file or vector (this happened for the return-label tests
and `customer-confirmation.v1.json`), never weaken the test and never put a real secret in a fixture. CodeQL flags a URL substring check
in a test (`"host" in url`): compare a parsed host or a label instead (chapter 36's check was changed for this). Fix the code, do not
dismiss the alert.

**Docker Desktop limits.** The engine can wedge when its disk fills (build cache grows into the tens of GB): container API hangs while
`docker info` still answers, and Testcontainers then fails every test in milliseconds. `docker info --format '{{.ServerVersion}}'` empty
means the engine is down. Prune build cache; only the owner restarts Docker Desktop. Two full stacks plus Testcontainers at once exceed
a laptop's memory: boot one stack at a time (`./scripts/dev.sh --dotnet --switch` tears the other down). CI on GitHub is the fallback
for .NET and Postgres-backed tests.

**Shared resources on one machine and parallel lanes.** Specialists bind 8081 to 8086 and the MCP servers 9000 and 9001 (override with
`MCP_PRODUCT_PORT` and `MCP_INVENTORY_PORT`); Postgres is 5432. Two lanes cannot both run the replay gate, a boot smoke or the MCP
compare: give each lane its own compose project name (`-p <name>`) and its own database, and serialise anything that binds the fixed
ports with `flock /tmp/eca-evals.lock`. Never use the default compose project (volume `e-commerce-agents_pgdata` holds the owner's dev
data); `down -v` your own. `dotnet` under `flock` leaves MSBuild workers holding the lock: pass
`--disable-build-servers -p:UseSharedCompilation=false -nodeReuse:false`. Kill exact PIDs, never `pkill` by name.

**Never bypass a blocked push.** If a hook or a permission rule blocks `git push`, stop and report it. Do not reach for `--no-verify`,
a different remote, an API upload of the files, or changing settings. A local safety hook blocks a command line that contains both
"force" and "main" (even for a feature branch): `git push --force-with-lease origin <branch>` as its own command, only on your own PR
branch, and only when the user allowed a rewrite.

## 6. How work flows

- Branch from a freshly fetched `origin/main`, small PRs, squash merge on green CI through the merge helper above.
- Every PR appends to `CHANGELOG.md` `[Unreleased]` and often adds a parity row, so conflicts there are routine: keep both sides, keep
  rows in numeric order and unique, put the entry under the right heading (Added, Changed, Fixed, Known limits), and merge `main`
  just before pushing. Re-run the touched tests after resolving. Two lanes appended the same entry twice more than once; read the
  result for duplicates.
- Version bumps and the release belong to `docs/releasing.md`. Do not tag.
- After a PR merges, delete its remote branch only if the merged PR's head SHA equals the branch tip and there is no open PR from it.
  Never delete another session's worktree or branch.
- Do not put secrets in commits, settings or PR text. The owner's `.env` holds real keys; read single variables by name, never print them.
  No document names a cloud account, tenant, subscription or resource group.
- CI facts: the .NET job and the Python job are the slow ones. The mixed-stack smoke jobs (`py-net`, `net-py`, a2a and token
  variants) caught a real crash once (blank `DB_POOL_*` reaching Python). Every `ScriptedSpecialist` test class belongs in
  `ProcessEnvironmentCollection`.

## 7. Where things live

- Plan: `.claude/plans/remaining-work.md` (live block, Azure, Foundry and gateway milestones, backlog, constraints).
- Docs index: `docs/README.md`. Azure: `docs/azure-deployment.md`, `docs/troubleshooting.md`. Config: `docs/configuration.md`. Providers:
  `docs/model-providers.md`. Evals: `docs/agent-quality.md`, `docs/eval-scorecard.md`. Parity: `docs/parity-matrix.md` (57 rows,
  numbered 1 to 58, number 45 unused; the Summary is checked against the table by `build_docs_site.py --check`). Auth routes:
  `docs/auth-route-matrix.md`. Approvals: `docs/approvals.md`. Autonomy: `docs/event-autonomy.md`. Merchant: `docs/merchant-proposals.md`.
- Infra: `infra/` (Bicep, profiles, `ci/*.bicepparam`), `azure.yaml`, `scripts/azure/`, `scripts/azure-up.sh`, `scripts/azure-down.sh`.
  Offline checks: `.github/workflows/infra-offline.yml`; the deploy workflow `.github/workflows/azure.yml` is dispatch-only with an
  `azure-deploy` environment.

## 8. Suggested next steps, in order

1. Merge this documentation pass, then cut v1.5.0 from `docs/releasing.md` (the maintainer approves the `release` environment).
2. Reconcile #97's checklist against `main` (close it or split the remainder) and the other open issues in the table in section 3.
3. The Azure step, last: dry run, what-if and cost estimate, small deploy, sanity script, measured cost, teardown,
   `verify-clean.sh`; then close #89, #91 and the deploy chapter of #100, and write that chapter.
4. The .NET 11 move as its own PR series.
5. Close #103.

## 9. Documentation notes

**Wording rules used for reader-facing pages:** say what is true now (history goes in `CHANGELOG.md`); no plan or lane codes and no
pointers into `.claude/plans/`; open with what the page is and who it is for; payoff before caveats, caveats kept; no fact changed
without checking the code; second person, present tense, no sentence much over 35 words; no emojis; none of "leverage", "delve into",
"it is worth noting". "Course" for the chapters, "complete application" for the app; "tutorial" only as a search topic.

**Checks to run after any docs change:**

```bash
python3 scripts/build_docs_site.py --check
python3 scripts/check_tutorial_readmes.py --check
python3 scripts/check_tutorial_coverage.py --check
python3 scripts/check_tutorial_nav.py --check
python3 scripts/build_eval_scorecard.py --check
cd agents/python && uv run pytest tests/test_auth_route_matrix.py tests/test_docs_site.py -q
```

Time sinks: `uv lock` with a different local uv version rewrites unrelated markers in `agents/python/uv.lock` (for a version bump, edit the
one `version =` line); the parity matrix and `CHANGELOG.md` are touched by every lane. Check for stray editor or `sed -i` backup files
before committing (`CHANGELOG.md-e` was once committed by mistake).

## Progress log

Append newest first. One line per landed change: date, PR, what, and anything left unverified.

- 2026-11-24 (final correctness pass): `CHANGELOG.md [Unreleased]` regrouped as v1.5.0 release notes (duplicates merged, contradictions resolved, Known limits and a Database note added); `docs/security-guide.md` now states that `GUARDRAILS_BLOCK_ON_INJECTION=true` blocks with a fixed refusal (no custom middleware needed); `docs/model-providers.md` de-duplicated; duplicate parity rows 39 and 40 removed and row 58's migration number fixed (0020); CLAUDE.md, README, roadmap, plan and this file brought in line with `main`. Unverified: no model or container was run; the checks are the docs-site check and the two doc-linked test files.
- 2026-11-24: #299 order-risk review, damaged-item remedy and monthly budgets (#328; migration 0018, Python only). Left open in #299: a real carrier feed (external system), and fulfilment that honours `orders.on_hold`.
- 2026-11-24: #327 red-team judge dimension, multi-turn live cases, turn-level budget ceiling and `usage_logs.would_block` (migration 0020, parity row 58); #326 PO-value threshold for restock proposals (migration 0019, row 39); #313 merchant-ops specialist (row 57); #306 and #307 customer confirmation (migration 0016, row 56, both stacks); #320 MCP tools match the native ones; #314 `draft_seller_response` ownership; #301 static preview; #87 and #182 README GIF and stills.
- 2026-11-24: course chapters 33 (prompt injection and red team), 34 (structured output), 35 (memory) and 36 (model providers), Python only, keyless; the course is now 38 chapters. #100 stays open: the Azure deployment, durable workflows, Foundry evals and AG-UI chapters are not written. Unverified: OpenAI direct and Anthropic were never called, and chapter 36 says so.
- 2026-11-23: docs audit fixes, the live-eval findings and their fixes (#220, #222, #240 to #258), the first live eval run (about 1 USD, Azure gpt-5.1 with a gpt-4.1-mini judge), Foundry evaluators and community results (#216), the gateway and identities modules (#213, #214, #223), the http target and stack-parity job (#215, row 53), Dependabot on (#224 to #230). A watcher once merged #223 before its last fix was on the PR head, which is why the merge helper pins the head SHA.
- 2026-11-23: v1.4.0 published (release approved by the maintainer, announcement Discussion #204; `orchestrator`, `frontend` and `db-init` checked anonymously for `:v1.4.0` and `:latest` with `amd64` and `arm64`, the other nine packages not checked). #208 (Approvals page for staff), #209 (Compare renders cards) and #210 (polish) fixed in the web app.
- 2026-11-21: #194 .NET approval-timeout worker (row 36 Full); v1.4.0 tagged; execution plan M0 to M8 written; decisions D-1, D-6, D-7, D-8 recorded; prices moved out of code into `config/pricing/models.yaml`; handover document added.
