# Plan 14 — Pre-Azure Work

**Status:** proposed · **Date:** 2026-08-25 · **Effort:** ~3 days
**Parent:** [`../audit-2026-08-25-adoption-and-azure.md`](../audit-2026-08-25-adoption-and-azure.md)
**Blocks:** [`13-azure-deployment-and-foundry.md`](13-azure-deployment-and-foundry.md)

Everything here ships before any Azure work starts. Only item 4 touches application code.

**Why this order.** Roughly 161 unique visitors land here every 14 days and about 1% stay
(20 stars in 4.7 months). Azure will bring more people to the same landing surface. Fix the
landing first, or the extra traffic converts at the same rate.

**Baseline, measured 2026-08-25** — record it now so the exit criterion is checkable:

```
stars 20 · forks 2 · watchers 0 · open issues 2
views/14d 504 (161 unique) · clones/14d 1506 (164 unique)
referrers: chatgpt.com 114 · Bing 61 · Google 58 · github.com 29 · claude.ai 5
```

---

## Contents

| # | Item | Effort | Touches app code |
|---|---|---|---|
| [1](#1--publish-images-to-ghcr-on-every-main-push) | Publish images to GHCR on every `main` push | 3 h | No |
| [2](#2--docker-composedemoyml-that-pulls-instead-of-builds) | `docker-compose.demo.yml` that pulls | 2 h | No |
| [3](#3--the-6090-second-silent-clip) | 60–90 s silent clip in README + site home | 4 h | No |
| [4](#4--publish-the-orchestration-mode-benchmark) | Publish the orchestration-mode benchmark | 1 d | **Yes** |
| [5](#5--llmstxt-llms-fulltxt-and-robotstxt) | `llms.txt`, `llms-full.txt`, `robots.txt` | 2 h | No |
| [6](#6--community-surface) | Community files + enable Discussions | 1 h | No |
| [7](#7--tutorial-status-wording) | Fix the tutorial status wording | 30 m | No |
| [8](#8--readme-cut-and-envminimal) | Cut README to ~150 lines, add `.env.minimal` | 4 h | No |

**Suggested execution order:** 1 → 2 → 7 → 4 → 3 → 5 → 6 → 8.
Items 1/2/7 are mechanical. Item 4 needs a real API key and is best done in one sitting.
Item 3 comes *after* item 2 so the recording uses the fast path. Items 5/6/8 are a cleanup pass.

---

## 1 — Publish images to GHCR on every `main` push

### Current state

`.github/workflows/build-images.yml` resolves `should_push=true` only when the trigger is a
**semver tag** or a **manual dispatch with `publish=true`**. Every other run builds with
`load: true` and throws the image away. `docker-compose.yml` carries `image:` for only three
services — `pgvector/pgvector:pg16`, `redis:7-alpine`, `mcr.microsoft.com/dotnet/aspire-dashboard`.
Every application service has a `build:` block and no `image:`.

Net effect: **every person who clones this repo builds eight images from source before they see
anything.**

### The matrix is also incomplete

The workflow's matrix covers six images — `orchestrator` and the five specialists. The compose
file needs **ten**:

| Image | Build context | Build args |
|---|---|---|
| `orchestrator` | `./agents/python` | `AGENT_NAME=orchestrator`, `AGENT_PORT=8080` |
| `product-discovery` | `./agents/python` | `AGENT_NAME=product_discovery`, `AGENT_PORT=8081` |
| `order-management` | `./agents/python` | `AGENT_NAME=order_management`, `AGENT_PORT=8082` |
| `pricing-promotions` | `./agents/python` | `AGENT_NAME=pricing_promotions`, `AGENT_PORT=8083` |
| `review-sentiment` | `./agents/python` | `AGENT_NAME=review_sentiment`, `AGENT_PORT=8084` |
| `inventory-fulfillment` | `./agents/python` | `AGENT_NAME=inventory_fulfillment`, `AGENT_PORT=8085` |
| **`auth-server`** | `./agents/python` | `AGENT_NAME=auth_server`, `AGENT_PORT=8090` |
| **`mcp-product`** | `./agents/python`, `Dockerfile.mcp` | `MCP_PACKAGE=ecommerce-mcp-product`, `MCP_DIR=packages/mcp-product`, `MCP_MODULE=ecommerce_mcp_product.server:app` |
| **`mcp-inventory`** | `./agents/python`, `Dockerfile.mcp` | mirror of the above for inventory |
| **`frontend`** | `./web` | — |

The four in bold are new to the workflow. Without them the demo compose file still builds locally
and the whole item is pointless.

### Changes

**`.github/workflows/build-images.yml`**

1. Extend the matrix to all ten, adding a `context` / `dockerfile` / `build-args` field per entry
   so the three build shapes (agent, MCP, frontend) coexist in one matrix.
2. Change the push resolution to include a push to `main`:

   ```
   push + ref_type=tag                    -> push, tags :<version> and :latest
   push + ref_name=main                   -> push, tags :main and :sha-<7>
   workflow_dispatch + publish=true       -> push
   pull_request                           -> never push (fork PRs have no packages:write)
   ```
3. Keep the existing import smoke-test on the PR path. It only works with `load: true`, so it is
   skipped on push runs — that is already the current behaviour and is correct.

**Multi-architecture.** The workflow currently builds `linux/amd64` only, on `ubuntu-latest`. A
reader on Apple Silicon pulling an amd64-only image gets QEMU emulation: slow, and for the Python
agents, occasionally broken. Add `platforms: linux/amd64,linux/arm64` **on the push path only** —
arm64 under QEMU roughly triples build time, which is acceptable on `main` but not on every PR.

### Gotchas

- **GHCR packages are private by default.** The first push creates ten packages that anonymous
  users cannot pull, and `docker compose pull` fails with an authentication error that reads like
  a network problem. Each package's visibility must be set to public **once, manually**, in the
  package settings. This is the single most likely way this item silently fails.
- Link each package to the repository (`org.opencontainers.image.source` label) so the GHCR page
  shows the README and the MIT licence rather than an empty page.
- Add a retention policy or the `:sha-*` tags accumulate indefinitely.

### Done when

```bash
docker pull ghcr.io/nitin27may/e-commerce-agents/orchestrator:main   # succeeds when logged out
```

…for all ten images, on both amd64 and arm64.

---

## 2 — `docker-compose.demo.yml` that pulls instead of builds

### Goal

`docker compose -f docker-compose.demo.yml up` reaches a working chat in under two minutes on a
clean machine, with no local build.

### Changes

**New file `docker-compose.demo.yml`.** Same topology as `docker-compose.yml`, with three
differences:

1. Every application service gets `image: ghcr.io/nitin27may/e-commerce-agents/<name>:main` and
   **no `build:` block**.
2. **No profiles.** `docker-compose.yml` gates services behind `agents` / `frontend` / `seed` /
   `mcp` profiles, which is right for development and wrong for a demo. The demo file starts
   everything a first-time visitor needs, in one command, with no flags.
3. The seeder is ordered explicitly:

   ```yaml
   orchestrator:
     depends_on:
       seeder:
         condition: service_completed_successfully
   ```

   The seeder must finish before the orchestrator starts, and it must populate before the IVFFlat
   index is built — see the production bug already recorded in `remaining-work.md`, where an index
   created on an empty table returned unrelated products at similarity 0.000.

**`scripts/dev.sh` and `scripts/dev.ps1`** — add a `--demo` / `-Demo` flag alongside the existing
`--clean` / `--seed-only` / `--infra-only` / `--dotnet` in the `case $arg in` block at line 112.
It selects `docker-compose.demo.yml` and skips every build step. Update the usage text at line 121.

**`README.md` and `docs/quick-start.md`** — make the demo path the *first* thing offered, with the
build-from-source path second, labelled as the contributor path.

### Things that already work and must not be broken

- The seeder mounts `./scripts:/app/scripts:ro`. That works from a clone, so the seeder image does
  not need the scripts baked in. Keep the mount.
- The frontend image bakes `NEXT_PUBLIC_API_URL=http://localhost:8080` at build time. For the local
  demo that is exactly right. (It is *not* right on Azure — that is blocker B1 in plan 13, and it
  is out of scope here.)
- `db`, `redis` and `aspire` already pull public images. No change.

### Done when

On a machine with no local images and no repo cache:

```bash
git clone https://github.com/nitin27may/e-commerce-agents.git
cd e-commerce-agents && cp .env.minimal .env   # set OPENAI_API_KEY
docker compose -f docker-compose.demo.yml up
```

…reaches a signed-in chat turn with product cards in under two minutes, timed.

---

## 3 — The 60–90 second silent clip

### Goal

The first thing a visitor sees on the README and the site home is the application working.
Currently it is eleven badges and a static PNG.

### The shot list

One continuous take, no cuts, no narration, no intro card, no face, no music.

1. Signed-in chat. Type a product question. Cards render as the answer streams.
2. Open the mode switcher. Pick a workflow mode. Re-ask.
3. The orchestration graph animates node by node from live SSE events.
4. Ask for a return or refund. The workflow pauses on its HITL gate.
5. Go to `/runs`. The pending badge is visible. Click Approve.
6. The run resumes from the checkpoint and completes.

That sequence demonstrates streaming, generative UI, five orchestration modes, live graph
animation, human-in-the-loop, and checkpoint resume — the six things that make this repo different
— without a single word of explanation.

### Production notes

- Record at 1920×1080, then downscale. Browser at a large font size; text must be legible in a
  README embed at roughly 800 px wide.
- Use the demo compose file from item 2, with a real API key so latency looks real. Do not speed
  up the video — the honest pacing is part of the point.
- Deliver as **MP4**, not GIF. A GIF of this length is 8–15 MB and `docs/images` is already 17 MB;
  adding that to the repo hurts every clone.
- Host the MP4 as a **GitHub release asset** and embed by URL. Commit only a small poster PNG.
- On the Jekyll site, use a `<video>` tag with `autoplay muted loop playsinline` and the poster
  image, so it degrades to a still where autoplay is blocked.

### Placement

- `README.md` — immediately after the one-paragraph description, above the badges if the badges
  survive the item-8 cut.
- `docs/index.md` — same position, so the generated site home carries it too.

### Done when

The clip is the first visual element on both properties and plays without a click.

---

## 4 — Publish the orchestration-mode benchmark

**The highest value-per-day item in the audit, and the only one here that touches app code.**

### Why

The site claims the same question can be routed five ways and compared for latency. There is no
published result anywhere. A reader — or an LLM answering "which orchestration pattern should I
use?" — has nothing to cite. Given that chatgpt.com is the top referrer by a wide margin, a page
with real numbers is the single most linkable artifact this project can produce.

### What already exists

- `agents/python/evals/harness.py` — `ProductionRunner`, which drives the real orchestration path
- `agents/python/evals/run_evals.py` — CLI with `--agent`, `--dataset`, `--output-json`,
  `--pass-threshold`, `--use-llm-judge`, `--baseline`
- `agents/python/evals/datasets/orchestrator_routing.json` — the routing dataset
- `POST /api/orchestration/compare` (`orchestrator/routes/orchestration.py:87`) — runs one prompt
  through several modes and reports text, latency, steps and graph
- `shared/cost.py` — dollar estimation · `shared/usage_db.py` — token persistence

### What is missing

`run_evals.py` has **no `--mode` flag**, and `/compare` deliberately returns no `tokens`,
`est_cost_usd` or `grounding` — the CLAUDE.md notes those need Phase 3.5 / Phase 2 infrastructure.
So neither existing entry point produces the table.

### Changes

**New `agents/python/evals/benchmark_modes.py`.** A script, not a CI gate:

1. Take a fixed prompt set — start from `orchestrator_routing.json`, plus a handful of prompts
   that exercise the pre-purchase and return-replace workflows specifically, since a
   routing-only set will not reach them.
2. For each of the five modes (`tool`, `handoff`, `workflow:pre-purchase`,
   `workflow:return-replace`, `group-chat`), resolve it through `orchestrator.modes.get_mode()`
   and run every prompt N times (N ≥ 3; single runs are noise).
3. Record per run: wall-clock latency, prompt/completion tokens from `usage_logs`, estimated cost
   from `shared/cost.py`, step count, and the eval score from the existing scorers.
4. Emit `evals/results/mode-benchmark-<date>.json` and a Markdown table.

**New `docs/orchestration-benchmark.md`.** The published page. It must state, in the page itself:

- the model and provider used
- the date of the run and the git SHA
- the number of repetitions and how the p50 was taken
- the machine and network conditions
- **the exact command that reproduces it**

A benchmark without those is not a benchmark, and this repo's whole credibility rests on not
publishing claims it cannot back.

**Then link it from:** the README *Where to start* table, the site's Architecture index, and
`docs/concepts/06-orchestration-patterns.md`, which currently explains the patterns without
comparing them.

### Gotchas

- **This costs real tokens.** It cannot run under `LLM_PROVIDER=replay` — replay fixtures return
  instantly, so latency becomes meaningless. Budget for it and note the spend in the page.
- Warm the stack first. A cold first request measures Docker, not orchestration.
- `workflow:pre-purchase` and `workflow:return-replace` resolve a product_id / order_id out of the
  message themselves. Prompts that resolve nothing will skew those two modes. Verify the resolution
  succeeded before counting a run.
- Do **not** wire this into CI. It is a periodic, manual, real-key artifact. The PR gate stays the
  free replay-driven smoke suite.

### Done when

`docs/orchestration-benchmark.md` is live on the site with a five-row table, a reproduce command,
and full run provenance.

---

## 5 — `llms.txt`, `llms-full.txt` and `robots.txt`

### Why

chatgpt.com sent 114 views in 14 days, more than Google (58) or Bing (61) individually. LLM answers
are the primary discovery channel and the site publishes nothing designed for them. None of the
three files exists — `docs/llms.txt`, `_site_src/llms.txt` and `docs/robots.txt` are all absent.

### Changes

All in **`scripts/build_docs_site.py`**, inside `build()` (line 820), after `rendered` is populated
and the asset copy loop runs — the same place the sitemap work already lives.

**`llms.txt`** — the index. Site title, one-paragraph summary, then the 85 pages grouped by section
(Concepts / Tutorials / Architecture / Getting Started / Guides / Reference) as
`- [Title](url): description`. The description is already computed by `extract_description()`, so
this is assembly, not new logic.

**`llms-full.txt`** — every page body concatenated, in nav order, each under an `# Title` heading
with its canonical URL. Roughly 96,000 words / ~600 KB. That is well within what current models
ingest and it means one fetch gives a model the whole corpus.

**`robots.txt`** — explicit `Allow: /`, a `Sitemap:` line, and pointers to both llms files.

**Extend `--check`** so a page that lands in neither file fails the build, the same way the existing
front-matter and link checks do. A generator that silently drops pages is worse than no generator.

### Gotchas

- Both files must be **written into `_site_src/` after** the `shutil.rmtree(OUT_DIR)` on line ~873,
  or they are deleted before they are served.
- Jekyll will not copy an unknown top-level file unless it is listed in `include:` in `_config.yml`.
  Check `docs/_config.yml` and add all three, or they build locally and 404 in production.
- Strip Liquid and the `{% raw %}` wrappers from `llms-full.txt`. A model reading `{% raw %}` as
  content is being handed noise.

### Done when

All three resolve at the site root and `--check` fails if a page is missing from `llms.txt`.

---

## 6 — Community surface

### Current state

`.github/` contains exactly one entry: `workflows`. There is no `SECURITY.md`,
`CODE_OF_CONDUCT.md`, issue template, or PR template. Discussions are disabled. **Watchers: 0** —
on a repo that ships something meaningful roughly weekly, nobody has any way to be told.

### Changes

| File | Content |
|---|---|
| `SECURITY.md` | Supported versions, and **GitHub private vulnerability reporting** as the channel — do not publish a personal email |
| `CODE_OF_CONDUCT.md` | Contributor Covenant 2.1, unmodified |
| `.github/ISSUE_TEMPLATE/bug_report.yml` | Which stack (Python/.NET), which LLM provider, compose file used, `docker compose logs` excerpt |
| `.github/ISSUE_TEMPLATE/feature_request.yml` | Problem, proposal, which stack(s) it must land on |
| `.github/ISSUE_TEMPLATE/config.yml` | Blank issues off; link to Discussions for questions |
| `.github/pull_request_template.md` | The checklist already written in prose in `CONTRIBUTING.md` |

Enable Discussions:

```bash
gh api -X PATCH repos/nitin27may/e-commerce-agents -F has_discussions=true
```

Seed it with three posts so it is not an empty room: an announcement of v1.1, a "what should the
Azure deployment cover" thread, and a pinned "start here" post linking the concepts index.

### Note

The bug template asking which stack and which provider is not boilerplate. Half the plausible bug
reports against this repo will be provider or stack confusion, and asking up front is the
difference between a triageable report and three round trips.

### Done when

The repo's community-standards checklist is complete and Discussions has its first three threads.

---

## 7 — Tutorial status wording

### Current state

`tutorials/README.md` has a 34-row table with columns `| # | Chapter | Status | Companion post |`.
Nearly every Status cell reads `Draft` or `Code done · draft`, and nearly every Companion post cell
reads `not yet published`.

The chapters are finished. They ship runnable code, tests, and a CI gate
(`scripts/check_tutorial_readmes.py`, plus `tutorials.yml` building all 31 .NET projects). The word
"Draft" is describing the **blog posts**, and no reader can tell. A visitor's first impression of
the largest asset in the repo is 34 rows of unfinished work.

### Changes

Replace the two columns with four:

```
| # | Chapter | Python | .NET | Companion post |
```

- **Python** — `Runnable · tested in CI` for every chapter that has one
- **.NET** — `Runnable · tested in CI` (00–11), `Code, tests pending` (12–19),
  `Not yet ported` (22–32), `SDK blocker` (16, 20)
- **Companion post** — `Published` with a link, or `—`. Not "not yet published", which reads as a
  gap; an em dash reads as "optional extra, not applicable".

Add a one-line note above the table: *every chapter here is complete and gated in CI; the companion
post column tracks optional cross-posts on nitinksingh.com.*

Mirror the change in `_site_src/tutorials/` — it is generated from this file, so rebuild the site.

### Verified dependency

`scripts/check_tutorial_readmes.py` lints **per-chapter** READMEs against the `_template` contract
(it iterates chapter directories, reading `chapter_dir / "README.md"`). It does **not** parse this
index table. Changing the columns is safe and will not break CI.

### Honest exception

Chapter 21 (Capstone Tour) is genuinely a scaffold with no runnable code. Mark it
`Planned` and leave it marked. This item is about removing a false signal, not adding one.

### Done when

No finished chapter reads as unfinished, and chapter 21 still reads as planned.

---

## 8 — README cut and `.env.minimal`

### Current state

`README.md` is **724 lines / 47 KB**. The material a senior developer actually wants — grounding,
idempotency, HITL, rate limiting, tracing — sits in a Roadmap section that starts at line 608.
Almost nobody reaches line 608.

`.env.example` is **210 lines / 53 variables**. The quick start needs one of them set.

### The cut

Target ~150 lines. **Keep:**

- One-paragraph description
- The clip from item 3
- Run it — the demo compose path from item 2, four lines
- The *Where to start* table (this is the best structural element in the file — it stays)
- Two or three screenshots
- Pick your stack — Python or .NET, two sentences
- Links: docs site, concepts, tutorials, architecture, benchmark
- Badges, licence, credit

**Move to the site:** Project Structure, the full Tech Stack table, Port Map, Configuration, Demo
Scenarios, Test Users, Screens gallery, the whole Roadmap, MCP setup, the free/local LLM section.

### The trap

`scripts/build_docs_site.py::collect_pages()` builds the site from `docs/` and `tutorials/`.
**`README.md` is not a site source.** So "move it to the site" means writing the content into a
`docs/` page — it does not relocate itself. Before cutting anything, check where each section
already exists:

- Roadmap → largely duplicated in `.claude/plans/remaining-work.md`; needs a real `docs/roadmap.md`
- Port Map, Configuration → already in `docs/deployment.md`, just link it
- Demo Scenarios, Test Users, Screens → no home yet; needs a new `docs/demo-guide.md`
- MCP setup → already in `docs/mcp-integration.md`, just link it
- Free/local LLM → already in `docs/quick-start.md`, just link it

Two of those five need a new page written. Do that first, then cut. Cutting first orphans content.

### `.env.minimal`

Compose supplies defaults for nearly every variable (`${VAR:-default}` throughout). The only thing
a first run genuinely requires is an API key:

```dotenv
# The only variable a first run needs. Everything else has a working default —
# see .env.example for the full surface (auth modes, MCP, OAuth, telemetry).
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-your-key-here

# No key? Point at a local model instead — see docs/quick-start.md
# LLM_BASE_URL=http://localhost:11434/v1
```

Point the quick start at `.env.minimal`. Keep `.env.example` as the complete reference and say so
in a header comment.

### Done when

`README.md` is under ~160 lines, no section was deleted without a destination, and the quick start
requires exactly one variable to be set.

---

## Exit criterion

Ship all eight, wait two weeks, then re-measure:

```bash
gh api repos/nitin27may/e-commerce-agents \
  --jq '{stars:.stargazers_count,watchers:.subscribers_count,forks:.forks_count}'
gh api repos/nitin27may/e-commerce-agents/traffic/views --jq '{count:.count,uniques:.uniques}'
gh api repos/nitin27may/e-commerce-agents/traffic/popular/referrers
```

Compare against the 2026-08-25 baseline at the top of this document. The number that matters is
**stars per unique viewer**, currently around 1%.

- **It moves** → the funnel was the constraint. Start plan 13 (Azure) with confidence.
- **It does not move** → the constraint is upstream of this plan. Re-argue Azure before spending
  three weeks on it; the problem is more likely positioning or audience than deployment coverage.

Also watch **watchers** and the **referrer mix**. Watchers going from 0 to anything means the
community items worked. chatgpt.com holding or growing after item 5 means the llms files were
read.
