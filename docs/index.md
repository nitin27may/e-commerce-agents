# E-Commerce Agents

A multi-agent e-commerce platform built on **Microsoft Agent Framework**, in **Python and .NET**, with the concepts written out in full beside the code that runs them.

Six specialist agents collaborate over the A2A protocol to handle product discovery, orders, pricing, reviews, inventory and support. Five orchestration patterns are selectable at runtime from the same chat box, so you can watch the same question routed five different ways and compare what each costs.

This site is generated from the repository. Every page here is a file you can read in the repo, and every code pointer resolves to real source.

> **Newer than this?** The [AI Knowledge Hub](https://nitinksingh.com/ai-resources/) is the layer
> below: eleven modules and ten labs that go from running a model on your laptop to an agent in
> production, all free and local. Start there if "agent", "tool call" or "orchestration" are not
> yet familiar words — then come back here to see them doing real work at scale.

## Run it

Docker is the only requirement — no Python, .NET or Node needed, and
[no paid API key either]({{ site.baseurl }}/getting-started/quick-start.html#run-without-a-paid-api-key).

```bash
git clone https://github.com/nitin27may/e-commerce-agents.git
cd e-commerce-agents
cp .env.example .env          # add your OPENAI_API_KEY (or Azure OpenAI credentials)
./scripts/dev.sh              # builds, seeds, and starts everything
```

Then open **<http://localhost:3000>** and sign in as `alice.johnson@gmail.com` / `customer123`.

**On Windows**, `scripts/dev.sh` is a bash script and will not run in PowerShell — use the
PowerShell twin instead, which takes the same flags:

```powershell
Copy-Item .env.example .env    # then set OPENAI_API_KEY in .env
./scripts/dev.ps1
```

→ **[Full Quick Start]({{ site.baseurl }}/getting-started/quick-start.html)** — the .NET stack,
running without an API key, WSL2 notes, and what to do when something breaks.

---

## Where to start

| If you are… | Start here |
|---|---|
| New to agents — you have not built one before | [Concepts]({{ site.baseurl }}/concepts/) — what an agent is, why more than one, what a graph means here |
| Ready to build | [Tutorials]({{ site.baseurl }}/tutorials/) — 34 chapters, Python and .NET, each runnable without an API key |
| Wanting to run the application | [Getting Started]({{ site.baseurl }}/getting-started/) |
| Evaluating the architecture | [Architecture]({{ site.baseurl }}/architecture/) |
| Checking what the .NET stack covers | [Parity matrix]({{ site.baseurl }}/reference/parity-matrix.html) |

## What makes this different

Most Agent Framework samples show one pattern in isolation. This repo solves **one non-trivial domain five ways** — tool router, handoff mesh, two workflow graphs, and a group-chat round table — in a single running application, so the question practitioners actually have ("which one should I use?") has an answer with latency and token numbers attached.

It also does the parts samples usually skip: server-side grounding that checks the model's claims against the database before the answer leaves, guardrails that actually block, human approval on destructive actions, idempotency on refunds, and an eval harness that runs the production path rather than a copy of it.

## The four layers

Each explains the same ideas at a different depth, and each says where to go next:

- **[Concepts]({{ site.baseurl }}/concepts/)** — the idea, in plain language, with a diagram and a pointer to where it does real work.
- **[Tutorials]({{ site.baseurl }}/tutorials/)** — build it yourself, small, one mechanism at a time.
- **[Architecture]({{ site.baseurl }}/architecture/)** — how the whole system fits together.
- **The code** — [on GitHub](https://github.com/nitin27may/e-commerce-agents), doing it at full scale.

## Where the project is

**v1.1.** Both backends are live and stable; the pause-and-resume approval loop, server-side grounding, idempotency on money-moving actions, rate limiting and the five orchestration modes all ship on Python and .NET.

### Recently shipped

- Follow-up questions keep their context. Specialists had been receiving *no* conversation history on any browser-originated turn — deterministically, while every test passed.
- .NET runs now appear in the Aspire GenAI view; they were invisible there because the span naming didn't match the convention Aspire selects on.
- Semantic search works. It was dead under replay mode, and beneath that sat a production bug: an IVFFlat index built on an empty table, returning unrelated products.
- Promotions apply. The seeder and the reader disagreed about `promotions.rules` key names, so no promotion had ever applied correctly.
- This site is indexable — per-page metadata, and an accessible title on every one of the 71 diagrams.

### Coming next

- **.NET eval suite** — 6 of 7 datasets ported; the recording run, baselines and CI gate remain.
- **.NET tutorial coverage** — chapters 12–19 have code but no tests, and 22–32 have no `dotnet/` yet.
- **Composer UX** — contextual prompt suggestions, and a collapsed mode selector.
- **Search** — `search_products` is still `ILIKE`; full-text and hybrid retrieval are planned.

The full list, including gaps this page doesn't claim to cover, is in [`.claude/plans/remaining-work.md`](https://github.com/nitin27may/e-commerce-agents/blob/main/.claude/plans/remaining-work.md).
