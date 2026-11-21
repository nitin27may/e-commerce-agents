# Handover

Working notes for resuming this project in a fresh session (local or cloud). Read this first, then
`CLAUDE.md`, then `.claude/plans/remaining-work.md` (the one plan; its "Consolidated plan, 2026-11-20"
and "Progress" blocks are the live part). **Update the Progress log at the bottom of this file in the
same PR as any work you land.** Facts here were true on the date shown; verify a name or number against
the code before relying on it.

Last updated: 2026-11-21, main at `9ba0a9b`, release `v1.4.0` tagged at `9d83d45`.

## 1. State in one screen

- Repo `nitin27may/e-commerce-agents`, default branch `main`, squash-merge only, history is the record.
- **v1.4.0 is tagged.** `release.yml` waits for the maintainer's approval in the GitHub `release`
  environment before images publish (run 44931300199 at the time of writing). **That approval is the
  maintainer's click: do not approve it through the API unless they tell you to.** After it publishes:
  `docs/releasing.md` step 7, then a release announcement in Discussions (Announcements), pinned
  alongside discussion #188.
- About 31 PRs from the 2026-11 effort are merged (#158 to #194). Nothing of mine is open. The only open
  PRs at the time of writing were another session's docs PRs (#195, #196).
- Remote branches: `main` plus whatever is open. Delete a branch after its PR merges (see section 6).
- Two Claude sessions work in this repo: this "queue" session (features, CI, Azure, evals, cleanups) and
  `e-commerce-agents-ac` (landing pages, docs wording, the release). Coordinate with `SendMessage` to
  `uds:/tmp/cc-socks/58881.sock` (check `ListAgents` for the current address). It owns README, docs
  landing pages, `docs/roadmap.md`, `docs/parity-matrix.md` wording and the release procedure
  (`docs/releasing.md`); ask before restructuring those. **Its own handover is "Part IV: Documentation
  lane" at the end of `.claude/plans/remaining-work.md`** (arrives with PR #195): the doc/code mismatches
  it suspects but has not verified, the wording rules, and the checks to run after any docs change. Read
  it before touching docs.

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
| Live-model eval run | A working local Docker + the Azure OpenAI key in the owner's `.env` | Procedure in section 5. Never run in CI automatically. |
| R0 recording session (about $1 to $3) | A key; run by a person | Records demo fixtures into `agents/python/demo/fixtures/replay/`, tutorial fixtures for chapters 04, 05, 07, 12 to 14, 16, the demo GIF. Then add `COPY demo/` to `agents/python/Dockerfile`. |
| Keyed features: D-5b customer confirm, B-16 `GroupChatBuilder`, M-2 merchant agent, V-1a `ops-responder`, E-3b multi-turn evals, E-4 red-team judge, E-6, E-7 MCP-path gate, E-8 .NET eval datasets, typed `ProductFilters` DSL, B2 dual-stack parity in CI | A key; record all in one sitting so nothing is recorded twice | See plan Part IV workstreams B, D, E. |
| Foundry: evaluators adapter, model provider (`LLM_PROVIDER=foundry`), hosted agents | Azure subscription for the last two | Design rule: optional, off by default, behind an extra; the core eval path must stay free of Azure dependencies. |
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
- Aspire is pinned at 13.6.5, not 17.0.0 (17 obsoletes the AppHost SDK and changes the project model).
- Redis on Azure: a container app for the demo profile (Azure Cache for Redis is closed to new customers).
- Plan correction: a return over $500 routes to `manager`, which `support` cannot decide.

## 5. Procedures that took time to learn

**Replay gate** (keyless, the thing CI runs): `scripts/replay-evals.sh --setup` on a fresh database. Main
is clean (zero misses). A miss means a prompt, tool schema or tool output changed: re-record or
`uv run python -m evals.rehash_fixtures` (only if the response is untouched). Check
`uv run python -m evals.fixture_drift` first.

**Live eval against a real model** (costs a few dollars; the owner approved running it):
1. Use a clean worktree of `main`; `cd agents/python && uv sync --all-packages --extra dev`.
2. **Do not `source` the repo's `.env`.** In the owner's setup its `DATABASE_URL` points at a different
   project's database and the seeder truncates. Pick only the `AZURE_OPENAI_*` variables out of it and set
   `DATABASE_URL=postgresql://ecommerce:ecommerce_secret@localhost:5432/ecommerce_agents`,
   `REDIS_URL=redis://localhost:6379`, `LLM_PROVIDER=azure` explicitly.
3. `docker compose -p eca-live up -d db redis` (own project name), then `scripts.migrate`, `scripts.seed`,
   `scripts.generate_embeddings` (live embeddings, under a cent).
4. Start the five specialists (`AGENT_NAME=... AGENT_PORT=808x uv run --no-project uvicorn <name>.main:app`)
   in their own process groups, wait for `/health`, then per agent:
   `uv run python -m evals.run_evals --agent <a> --dataset evals/datasets/<a>.json --pass-threshold 0.7
   --use-llm-judge --baseline evals/baselines/<a>.json --max-regression 0.05 --verbose --output-json <out>`;
   orchestrator uses `orchestrator_routing.json`; safety uses `--suite safety --pass-threshold 0.8`. This
   mirrors the `full` job in `.github/workflows/evals.yml`. Do not pass `--update-baseline`.
5. Kill only the PIDs you started; `docker compose -p eca-live down -v`.
6. Record scores in `docs/eval-scorecard.md` terms (the generator reads committed baselines; extending it
   to accept a live-result file is a good small follow-up).

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
  `docs/eval-scorecard.md`. Parity: `docs/parity-matrix.md` (rows to 49 plus). Auth routes:
  `docs/auth-route-matrix.md`. Approvals: `docs/approvals.md`.
- Infra: `infra/` (Bicep, profiles, `ci/*.bicepparam`), `azure.yaml`, `scripts/azure/`, `scripts/azure-up.sh`,
  `scripts/azure-down.sh`. Offline checks: `.github/workflows/infra-offline.yml`; deploy workflow
  `.github/workflows/azure.yml` is dispatch-only with an `azure-deploy` environment.

## 8. Suggested next steps, in order

1. Confirm the v1.4.0 release environment approval happened and images published.
2. Get Docker healthy, run the live eval, and record the scores (see section 5).
3. Optional Foundry evaluators adapter and a live-result file format for the scorecard.
4. Decide D-1; do the first real Azure deploy with `scripts/azure-up.sh --dry-run` first.
5. R0 recording session, then screenshots and the demo GIF (#182), then remove the docs caveats.
   Click through `demo-guide` scenarios 9 to 11 once the stack runs.
6. The keyed features in section 3, recorded in one sitting.

## Progress log

Append newest first. One line per landed change: date, PR, what, and anything left unverified.

- 2026-11-21: #194 .NET approval-timeout worker (row 36 Full); v1.4.0 tagged; handover document added.
- 2026-11-21: #158 to #193: see the plan's Progress block for the list.
