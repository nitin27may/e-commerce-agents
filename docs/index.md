# E-Commerce Agents

A complete, end-to-end multi-agent e-commerce application built on **Microsoft Agent Framework**, in **Python and .NET**, and a 38-chapter course that teaches every piece of it. It is a working application with a storefront, sign-in and roles, a database, approvals, evals and tracing, with the concepts written out in full beside the code that runs them.

An orchestrator and five specialist agents collaborate over the A2A protocol to handle product discovery, orders, pricing, reviews, inventory and support. Five orchestration patterns are selectable at runtime from the same chat box, so you can watch the same question routed five different ways and compare what each costs.

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
cp .env.minimal .env          # set OPENAI_API_KEY (Azure OpenAI and the rest: .env.example)
./scripts/dev.sh --demo       # pulls prebuilt images, seeds, and starts everything
```

Then open **<http://localhost:3000>** and sign in as `alice.johnson@gmail.com` / `customer123`.

**On Windows**, `scripts/dev.sh` is a bash script and will not run in PowerShell — use the
PowerShell twin instead, which takes the same flags:

```powershell
Copy-Item .env.minimal .env    # then set OPENAI_API_KEY in .env
./scripts/dev.ps1 -Demo
```

→ **[Full Quick Start]({{ site.baseurl }}/getting-started/quick-start.html)** — the .NET stack,
running without an API key, WSL2 notes, and what to do when something breaks.

---

## Where to start

| If you are… | Start here |
|---|---|
| New to agents — you have not built one before | [Concepts]({{ site.baseurl }}/concepts/) — what an agent is, why more than one, what a graph means here |
| Ready to build | [Tutorials]({{ site.baseurl }}/tutorials/) — 38 chapters, Python and .NET (the last four Python only), each runnable without an API key |
| Wanting to run the application | [Getting Started]({{ site.baseurl }}/getting-started/) |
| Choosing an orchestration pattern | [Orchestration patterns]({{ site.baseurl }}/concepts/06-orchestration-patterns.html) and the [benchmark]({{ site.baseurl }}/reference/orchestration-benchmark.html): the same question routed five ways, with latency and cost |
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

**v1.4 is the latest release.** Both backends are live and stable; the pause-and-resume approval loop, server-side grounding, idempotency on money-moving actions, rate limiting and the five orchestration modes all ship on Python and .NET.

### New in 1.4

See the [roadmap]({{ site.baseurl }}/getting-started/roadmap.html) and the [changelog](https://github.com/nitin27may/e-commerce-agents/blob/main/CHANGELOG.md) for the full entries:

- **A2A 1.0** on both stacks: JSON-RPC at `/a2a/v1`, an agent card, and a signed user-context token, beside the original wire.
- **Approvals**: one inbox and one decide path, routed by policy, with SLA timers run by an outbox worker.
- **Merchant proposals, event autonomy and the return lifecycle** (Python): what the system may do on its own, under budgets, with every action logged and reversible.
- **An Anthropic provider** and a separately configured embeddings provider.
- **A DevUI entry point** for browsing the specialists and workflows.

### Coming next

- **Azure** — the Container Apps deployment is written and checked offline but has not run against a real subscription yet; that first deploy, then Foundry as a model provider.
- **A model-calling parity run** — the keyless parity gate already runs in CI (`stack-parity.yml`); the live dual-backend browser suite (`scripts/e2e-both-stacks.sh`) still runs locally.
- **.NET eval suite** — 6 of 7 datasets ported; the recording run, baselines and CI gate remain.

The full list, including what is deliberately not planned, is on the [roadmap]({{ site.baseurl }}/getting-started/roadmap.html).
