# E-Commerce Agents

A multi-agent e-commerce platform built on **Microsoft Agent Framework**, in **Python and .NET**, with the concepts written out in full beside the code that runs them.

Six specialist agents collaborate over the A2A protocol to handle product discovery, orders, pricing, reviews, inventory and support. Five orchestration patterns are selectable at runtime from the same chat box, so you can watch the same question routed five different ways and compare what each costs.

This site is generated from the repository. Every page here is a file you can read in the repo, and every code pointer resolves to real source.

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
