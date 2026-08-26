# Audit — Adoption, Learnability, and the Azure Gap

**Date:** 2026-08-25 · **Scope:** whole repository + the generated docs site + the GitHub surface
**Session:** e-commerce-agent-enhancement
**Method:** read the repo as four different readers would, then check the claims against the code,
CI, GitHub traffic API, and the current Microsoft Foundry / Agent Framework documentation.

This is an audit of **whether the work lands**, not of whether the code is correct. Correctness is
tracked separately in [`remaining-work.md`](remaining-work.md), and the short version of that
document is: it is in good shape.

---

## 1. The numbers, first

Everything below is measured, not estimated.

| Signal | Value | Source |
|---|---|---|
| Repository age | 4.7 months (created 2026-04-05) | GitHub API |
| Stars / forks / watchers | **20 / 2 / 0** | GitHub API |
| Views, last 14 days | 504 (**161 unique**) | GitHub traffic API |
| Clones, last 14 days | 1,506 (164 unique) — mostly own CI | GitHub traffic API |
| **Top referrer** | **chatgpt.com — 114 views, 37 unique** | GitHub traffic API |
| Next referrers | Bing 61/12, Google 58/25, github.com 29/10, claude.ai 5/1 | GitHub traffic API |
| Python backend | ~36,400 LOC | `find agents/python -name '*.py'` |
| .NET backend | ~31,000 LOC | `find agents/dotnet -name '*.cs'` |
| Frontend | ~19,400 LOC | `find web/src` |
| Docs prose | ~43,500 words (`docs/`) + ~53,000 words (tutorial READMEs) | `wc -w` |
| Diagrams | 71 Mermaid, 17 screenshots (17 MB) | build script, `docs/images` |
| Docs site | 85 pages, live, indexed | `nitinksingh.com/e-commerce-agents` |
| Open issues | 2 tracked ( #4, #20 ) | `gh issue list` |

Three of these deserve a second look.

**chatgpt.com is the number one referrer, ahead of Google and Bing.** People are finding this repo
by asking a model, not by searching. That is the discovery channel, and the repo is not optimised
for it — there is no `llms.txt`, no `llms-full.txt`, and the README's structured summary is buried
under eleven badges and a 47 KB body.

**161 unique viewers in 14 days, 20 stars in five months.** Extrapolating, roughly 1,700–2,000 people
have looked at this repo and about 1% bookmarked it. The traffic is not the problem. The landing is.

**0 watchers.** Nobody has subscribed to updates on a repo that ships something meaningful roughly
weekly. Discussions are disabled, there are no issue templates, no `SECURITY.md`, no
`CODE_OF_CONDUCT.md` — none of the signals that tell a visitor "this is a maintained project you can
participate in", and none of the surfaces that generate return visits.

---

## 2. The finding, in one paragraph

**This is not a quality problem, and it is not a content problem. It is a conversion problem, and
one specific content gap.** The engineering is well past the level of most Agent Framework material
in public — dual-stack parity gated by a real E2E suite, an eval harness that drives the production
path, server-side grounding, HITL with checkpoint resume, idempotency on refunds, OAuth2 with a
self-hosted issuer. The prose is genuinely good; the concepts pages are the best writing in the repo.
What is missing is (a) any way for a visitor to see it working inside 60 seconds, and (b) the one
question the target audience actually arrives with, which is **"how do I run this on Azure?"** —
a question the repository currently answers nowhere at all.

---

## 3. Four readers

I walked the repo as each of these, entering where they would actually enter (a search engine or an
LLM answer, landing on the README or the docs site home).

### Reader A — never built anything with AI

**Wants:** to understand whether this is for them, and to see something happen.

**Where they land well.** The docs site home has an explicit "Newer than this?" callout pointing to
the AI Knowledge Hub, which is the right call and honestly made. `docs/concepts/01-what-is-an-agent.md`
is the strongest single page in the repo — the intern/manager analogy, the explicit "none of those
three things is optional", the refusal to hand-wave. If they reach that page they will keep reading.

**Where they stop.**

1. **They cannot see it.** The only proof the thing works is a static PNG. To see a real agent turn
   they must install Docker, obtain an OpenAI key, clone 24 MB, and wait through a build of eight
   images plus a pgvector seed. That is a 10–20 minute commitment against an unknown payoff, from a
   person who does not yet know what an agent is. Almost none of them will do it.
2. **The path is a link out with no return.** Hub → Concepts → Tutorials → Capstone is the actual
   ladder, and it is never drawn. The Hub link leaves the property; nothing brings them back.
3. **Fourteen concepts pages with no ordering signal.** No reading time, no "read these four first",
   no indication that 01/02/03/05 is a complete arc and the rest is depth.

**Fix.** A 60–90 second silent screen capture at the top of the README and the site home. One
"read these four" strip on the concepts index. An explicit four-rung ladder diagram, drawn once,
reused on both properties.

### Reader B — senior developer, 15+ years, new to AI

This is the largest and most valuable segment, and it is the reader the repo is closest to serving
and furthest from converting.

**Wants:** to skip the fundamentals entirely and see how a real system is wired — auth, tracing,
cost, failure handling, deployment. Then to take it to work.

**Where they land well.** Everything they want exists. Distributed tracing with GenAI semantic
conventions and `trace_id` correlated into `usage_logs`. Idempotency keys on money-moving actions.
Circuit breakers and jittered backoff on every A2A hop. A Redis sliding-window limiter. HITL with a
claim-before-execute so a double click cannot double-refund. An OAuth2 authorization server living
inside the repo. This is the material almost no sample has.

**Where they stop.**

1. **The Azure question has no answer.** `docs/deployment.md` is 428 lines and 100% local Docker
   Compose. There is no Bicep, no `azure.yaml`, no Terraform, no ACA, no AKS, no Helm chart, no
   managed identity, no Key Vault wiring, no Foundry. The repository topics include `azure-openai`;
   the security guide advises storing `JWT_SECRET` in Azure Key Vault — and then offers no path to
   doing so. For a reader whose day job is Azure, this is where the repo stops being usable and
   becomes a curiosity. **This is the single largest gap in the project.**
2. **The README is 724 lines / 47 KB.** Everything they need is in there, and it is unfindable. The
   production-concerns material — the actual reason a senior dev would care — sits below a Roadmap
   section that begins at line 608.
3. **No decision trail.** The one genuinely excellent architectural argument in the repo ("Text-to-SQL
   was considered and rejected: `user_email`/`user_role` scoping via ContextVars means dynamic SQL
   would bypass that contract") is three lines inside the Roadmap. There is no ADR directory. A
   senior reader evaluates a codebase by its rejected options, and this repo has good ones hidden.

**Fix.** Azure deployment as a first-class, runnable artifact — see section 6 and the companion plan.
README cut to roughly 150 lines with everything else pushed to the site. An `docs/adr/` directory
seeded with the five decisions already argued in prose: A2A over direct calls, no text-to-SQL, YAML
prompt composition, MAF-native execution over a hand-rolled tool loop, dual-stack parity.

### Reader C — mid-level developer starting their AI journey

**Wants:** a sequenced path with runnable code at every step.

**Where they land well.** 34 tutorial chapters, Python throughout, .NET through chapter 21, each with
tests, each CI-gated by `check_tutorial_readmes.py` and the tutorials workflow. This is a serious
asset and it is rare.

**Where they stop.**

1. **The status table tells them the work is unfinished when it is not.** Every chapter reads
   `Draft` or `Code done · draft`, and every companion post reads `not yet published`. Thirty-four
   rows of "draft" is the first thing this reader sees. The code is done, tested, and gated in CI —
   the vocabulary is describing the blog posts, not the chapters, and the reader cannot tell.
   **This is the cheapest high-impact fix in the audit: perhaps 30 minutes.**
2. **Chapter 21, "Capstone Tour", is a scaffold with no runnable code.** That is precisely the bridge
   from "I did 20 small exercises" to "I understand the application" — the chapter that converts a
   tutorial reader into a repo user. It is the one missing rung on the ladder.
3. **No time estimates and no dependency graph.** Chapter 24 (RAG) does not need chapters 12–19.
   Nothing says so, so the reader assumes a 34-chapter linear commitment and does not start.

**Fix.** Rewrite the status column vocabulary to describe reality (`Runnable · tested in CI`), split
the companion-post column so an unpublished post does not read as an unfinished chapter, add per-chapter
minutes, add a "three tracks" view (fundamentals / orchestration / production), and write chapter 21.

### Reader D — already builds agents, evaluating whether to trust this

**Wants:** numbers, and evidence that the hard parts are real rather than described.

**Where they land well.** They will find the eval harness immediately and they will respect it —
particularly that it drives the production orchestration path rather than a copy, and that the smoke
gate runs on committed replay fixtures with no API key. The `remaining-work.md` "reported problem was
smaller than the actual one" table is the most credible thing in the repository, because it is a
public record of five bugs that all tests passed through. That page should be linked from the README,
not buried in `.claude/`.

**Where they stop.**

1. **There are no numbers.** The site claims the same question can be routed five ways and compared
   for latency. There is no published result. No table of tool-router vs handoff vs pre-purchase
   workflow vs return-replace vs group-chat across p50 latency, tokens, estimated cost, and eval
   score. **The infrastructure to produce this already exists and is already wired** — `evals/harness.py`
   plus the `/api/orchestration/compare` endpoint plus `shared/cost.py`. This is roughly one day of
   work and it is the single most linkable, most quotable artifact the project could publish. It is
   also exactly what an LLM will cite when someone asks "which orchestration pattern should I use?" —
   which matters given that chatgpt.com is the top referrer.
2. **Guardrails default to fail-open and there is no red-team dataset yet.** Both are disclosed
   honestly. A skeptical reader will still want the false-positive number that justifies the default.
3. **`search_products` is still `ILIKE`.** Disclosed, but it is the workhorse tool and it will be
   noticed. It slightly undercuts the retrieval story that pgvector otherwise tells well.

**Fix.** Publish the mode benchmark as a versioned page with the command that reproduces it.

---

## 4. Findings, by severity

Effort is focused working time, not calendar time.

| # | Finding | Evidence | Fix | Effort |
|---|---|---|---|---|
| **F1** | **No Azure deployment path exists.** No Bicep, no `azure.yaml`, no Terraform, no K8s manifests, no Foundry integration. `docs/deployment.md` is entirely local Compose. | `find . -iname '*.bicep' -o -iname 'azure.yaml' -o -iname 'main.tf'` returns nothing | `infra/` Bicep + `azd up`, ACA topology, Foundry variant — see plan 13 | 5–8 d |
| **F2** | **First run requires a full local build of 8+ images.** `build-images.yml` only pushes to GHCR on a semver tag or manual dispatch, and `docker-compose.yml` carries no `image:` for any app service — so every visitor rebuilds from source. | workflow `on:` block; `grep image: docker-compose.yml` returns only postgres/redis/aspire | Push to GHCR on every `main` push; ship `docker-compose.demo.yml` that pulls | 3 h |
| **F3** | **Nothing to look at without installing.** No hosted demo, no video, no GIF. First-run proof is a static PNG. | README, site home | 60–90 s silent capture at the top of both; hosted demo decision below | 4 h (capture) |
| **F4** | **No `llms.txt` / `llms-full.txt`,** while chatgpt.com is the #1 referrer by a wide margin. | traffic API; `ls docs/llms.txt` → absent | Generate both from `build_docs_site.py` alongside the sitemap | 2 h |
| **F5** | **No published orchestration-mode benchmark.** The comparison feature ships; the result does not. | `/api/orchestration/compare` exists, no results artifact anywhere | One page, generated by the existing harness, with the reproduce command | 1 d |
| **F6** | **No community surface.** Discussions off, 0 watchers, no `SECURITY.md`, `CODE_OF_CONDUCT.md`, issue templates, or PR template. | `ls .github/` → `workflows` only | Add all five; enable Discussions | 1 h |
| **F7** | **Tutorial status vocabulary undersells finished work.** 34 rows of "Draft" / "not yet published" describing CI-gated, tested chapters. | `tutorials/README.md` status table | Rewrite the column; split chapter status from post status | 30 min |
| **F8** | **README is 724 lines / 47 KB.** The production material a senior reader wants starts around line 600. | `wc -l README.md` | Cut to ~150; push the rest to the site, which already renders it | 3 h |
| **F9** | **Chapter 21 (Capstone Tour) is an empty scaffold** — the bridge from tutorials to the application. | `tutorials/README.md` status; folder has no runnable code | Write it | 1–2 d |
| **F10** | **No ADR trail.** Five well-argued decisions exist as prose fragments inside README/CLAUDE.md. | `docs/` has no `adr/` | `docs/adr/`, five records, mined from existing prose | 4 h |
| **F11** | **`.env.example` is 210 lines / 53 variables** for a quick start that needs one. | `wc -l .env.example` | Ship `.env.minimal` (4 lines) as the quick-start default; keep the full file as reference | 1 h |
| **F12** | **`remaining-work.md` — the most credible artifact in the repo — is invisible.** It lives in `.claude/` and is linked once, from the Roadmap. | README line ~610 | Promote the "reported vs actual" table into the docs site as its own page | 2 h |

---

## 5. What is strong — do not break it in the process

Listing this explicitly because several of the fixes above involve cutting, and the wrong cut would
damage the thing that makes this repo worth finding.

- **The prose voice.** Direct, specific, willing to say a thing was broken and for how long. The
  concepts pages and the `remaining-work.md` failure table are the differentiator. Every competing
  Agent Framework sample is written in marketing register; this one is not.
- **The dual-stack parity gate.** Python and .NET behind one frontend, enforced by a dual-backend
  Playwright run rather than a checklist, with a parity matrix that lists what still differs. Nobody
  else has this.
- **The eval harness driving the production path.** Including replay fixtures that make the smoke
  gate free and key-less.
- **Solving one domain five ways.** This is the actual thesis of the project and it is correct. It is
  also currently asserted rather than demonstrated — see F5.
- **The honesty of the roadmap.** Unshipped things are marked unshipped, with the reason. Keep that
  exactly as it is.

---

## 6. The Azure gap — shape of the answer

Full design in [`enhancements/13-azure-deployment-and-foundry.md`](enhancements/13-azure-deployment-and-foundry.md).
The summary of the recommendation:

**Do both Azure Container Apps and Microsoft Foundry, in that order, and treat the difference between
them as the content.** They answer different questions, and the comparison is worth more than either
one alone.

- **Topology 1 — Azure Container Apps.** The "lift the Compose file properly" path: twelve container
  apps into one ACA environment, ACR, PostgreSQL Flexible Server with the `vector` extension, Azure
  Cache for Redis, Azure OpenAI, managed identity throughout, Key Vault for the JWT signing secret,
  internal ingress replacing the `AGENT_REGISTRY` hostnames, and Azure Monitor / Application Insights
  replacing the Aspire OTLP sink. Delivered as `infra/*.bicep` + `azure.yaml` so the whole thing is
  `azd up`. **This is what 90% of the target audience needs and cannot currently get from this repo.**

- **Topology 2 — Microsoft Foundry.** Two sub-variants, and both are worth showing because the
  difference between them is exactly what people are confused about:
  - **Foundry as model provider only** — an `LLM_PROVIDER=foundry` seam using `FoundryChatClient`
    (Python) / `AIProjectClient.AsAIAgent(...)` (.NET). Small diff, real payoff: the same six agents
    gain access to Foundry's hosted tools (web search, code interpreter, Azure AI Search, memory).
  - **Foundry Hosted Agents** — package the orchestrator as a hosted agent container via
    `azd ai agent init` / `provision` / `deploy`, with the five specialists staying on ACA and wired
    back in as A2A tools. Hosted Agents is GA; the Agent Framework hosting packages are prerelease.

**The finding that makes the write-up worth reading**, and which no existing post covers: the Foundry
**Responses** protocol is OpenAI-shaped and manages history, streaming and session lifecycle for you —
which means it **cannot carry this repo's custom SSE frames** (`node`, `handoff`, `checkpoint`,
`step`). The existing frontend would lose the live graph animation and the approval flow. The
**Invocations** protocol gives full control of the HTTP response and can. Choosing between them is a
real architectural trade — managed session state versus your own event contract — and it is the kind
of concrete "here is what you give up" detail that gets a page bookmarked.

**Cost, for the record-then-purge plan.** With scale-to-zero on ACA, Postgres Flexible Server B1ms,
Redis Basic C0 and ACR Basic, a four-hour recording session lands in the low single-digit dollars.
Left standing it is roughly $40–60/month before token spend. A `scripts/azure-down.sh` that deletes
the resource group is part of the deliverable, as requested — and it should be written and tested
*before* the first `azure-up.sh` run, not after.

---

## 7. The demo recording — honest take

Yes, but not one video, and not first.

**Do immediately: a 60–90 second silent loop.** No narration, no intro, no face. Type a question,
watch the mode switcher, watch the graph animate node by node, watch product cards render, hit an
approval gate, approve it, watch it resume. Embedded at the top of the README and the site home.
This is the conversion fix from F3 and it is worth more than a 20-minute walkthrough, because the
problem is not that people watch and leave — it is that they never see anything at all.

**Do after the Azure work: the long-form recording.** And record the *deployment*, not the app.
"Deploy a six-agent system to Azure Container Apps, then move the orchestrator to Foundry Hosted
Agents, and here is what breaks" is a searchable topic with a real audience and almost no good
existing material. "Here is my e-commerce agent demo" is not a searchable topic and competes with
thousands of others. The app demo is the first 90 seconds of that video, not the subject of it.

**On a permanently hosted live demo.** Tempting and I would not do it yet. It means an exposed
LLM key with no spend ceiling, a public write surface, and an ongoing bill for a project with 20
stars. Revisit at the point where traffic justifies it. The recorded loop gets 90% of the benefit
at 0% of the risk.

---

## 8. Recommended order

### Wave 0 — conversion (about 2 days, all cheap, all independent)

Nothing here touches application code. Every item is a landing-surface fix.

1. GHCR images on every `main` push + `docker-compose.demo.yml` that pulls instead of builds (F2)
2. 60–90 s silent capture at the top of README and site home (F3)
3. `llms.txt` + `llms-full.txt` generated by the site build (F4)
4. `SECURITY.md`, `CODE_OF_CONDUCT.md`, issue + PR templates, enable Discussions (F6)
5. Tutorial status vocabulary (F7)
6. README cut to ~150 lines (F8)
7. `.env.minimal` (F11)

### Wave 1 — the Azure answer (about a week)

8. `infra/` Bicep + `azure.yaml`, ACA topology, managed identity, Key Vault (F1)
9. `scripts/azure-down.sh` first, then `azure-up.sh`
10. New site page: "Deploy to Azure" — topology diagram, cost table, teardown
11. Record the deployment walkthrough

### Wave 2 — the differentiator (about a week)

12. `LLM_PROVIDER=foundry` provider seam, Python and .NET
13. Orchestrator as a Foundry Hosted Agent; specialists remain on ACA, wired back over A2A
14. The comparison write-up: local vs ACA vs Foundry — what you gain, what you give up, what it costs

### Wave 3 — credibility artifacts

15. Publish the orchestration-mode benchmark (F5)
16. `docs/adr/` seeded with five existing decisions (F10)
17. Promote the "reported vs actual" table onto the docs site (F12)
18. Chapter 21, Capstone Tour (F9)

---

## 9. What I would deliberately deprioritise, and why

Stating this plainly because it contradicts the current `remaining-work.md` ordering.

- **.NET tutorial coverage (#20) — chapters 12–19 tests and 22–32 `dotnet/`.** This is described as
  "the largest single piece of work left in the repo" and it is. It is also the item with the worst
  effort-to-adoption ratio on the list: multiple weeks of work that no visitor is currently blocked
  on, on chapters most readers will never open. It matters for the integrity of the parity claim, not
  for adoption. Keep it open, ship it incrementally, do not let it sit in front of Wave 0 or Wave 1.

- **The .NET eval suite recording run.** Correctness work, genuinely valuable, invisible to every
  reader. It belongs after the Azure work.

- **Composer UX (#4).** Real polish, small reach. It is a two-hour job that will not move a single
  bookmark. Fold it into Wave 0 only if it is genuinely quick.

The general principle: **not one item currently at the top of `remaining-work.md` will move
adoption.** They are all engineering debt of the good kind — the kind that keeps a project honest —
but the repo's constraint right now is that 1,700 people looked and 20 stayed, and none of those
items address that.

---

## 10. Open questions

These change the shape of the work, so they are worth deciding before Wave 1 starts.

1. **Azure subscription and budget.** Which subscription, what monthly ceiling, and is a spend alert
   already in place? The teardown script assumes a dedicated resource group per deployment.
2. **Does the existing frontend have to work unchanged against Foundry?** If yes, the Foundry variant
   must use the Invocations protocol and the write-up changes shape. If a reduced-fidelity Responses
   variant is acceptable for the demo, the work is roughly half.
3. **Which stack gets the Azure treatment first — Python or .NET?** The audience skew argues .NET.
   The repo's own maturity argues Python. Doing both doubles Wave 1.
4. **Does Azure deployment ship as `azd` only, or `azd` plus raw Bicep plus a GitHub Actions deploy
   workflow?** The third is what an enterprise reader actually copies, and it is the most work.
5. **Foundry region and model availability** — the Foundry project region constrains which models and
   which hosted tools are available, which in turn constrains what the comparison page can claim.
6. **Is `nitinksingh.com/e-commerce-agents` the canonical home, or does the Azure content want its own
   long-form post?** Cross-posting strategy affects how the deployment doc is written.
7. **How much of the Azure work should generalise?** A `deploy-maf-to-azure` reference that other
   people can lift is worth more than a deployment of this one app — but it is a different artifact
   with a different scope.

---

*Companion plan: [`enhancements/13-azure-deployment-and-foundry.md`](enhancements/13-azure-deployment-and-foundry.md)*
