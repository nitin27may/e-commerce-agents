# Concepts

This is the foundations layer: the ideas behind multi-agent systems, explained for a developer
who is competent but new to the AI/agent world. You do not need to know what an agent is, what
"multi-agent" means, or what a guardrail is for before you start — every term is defined before
it is used.

Every page follows the same shape:

- **What it is** — plain language, one honest analogy, no jargon in the definition itself.
- **Why it matters** — the concrete problem it solves, and what breaks if you skip it.
- **When to use it — and when not to.** Explicit non-use cases.
- **How it works here** — the pattern running in *this* application, with a `file:line` pointer
  into the real code and a diagram of the real path.

The repository is the source of truth for these ideas. Nothing here depends on an external
article — read this, then read the file it points at, and you have the whole picture.

## Two reading paths

**New to agents — start here.** Read in order:

1. [What is an agent](01-what-is-an-agent.md)
2. [The agentic loop](02-the-agentic-loop.md)
3. [Tools](03-tools.md)
4. [The agent harness](04-agent-harness.md)
5. [Why multi-agent](05-why-multi-agent.md)
6. [Orchestration patterns](06-orchestration-patterns.md)
7. [Graphs in agent systems](07-graphs-in-agent-systems.md)

By page 7 you have the full vocabulary this repo uses, and you can either keep going below or
jump straight to [`docs/architecture.md`](../architecture.md) to see how the six agents in this
codebase are actually wired together.

**Already know agents — show me the system.** Skip straight to
[`docs/architecture.md`](../architecture.md) for the system-level view, and come back to
individual pages here when a term needs unpacking.

## The rest of the pages

| Page | What it answers |
|---|---|
| [08 — State, memory, and sessions](08-state-memory-and-sessions.md) | Why agents are stateless by default, and the three different things people call "memory" |
| [09 — Grounding and RAG](09-grounding-and-rag.md) | Why models fabricate, and the difference between retrieving data and verifying a claim |
| [10 — Guardrails](10-guardrails.md) | The threat model in plain terms, and what each defensive layer can and can't stop |
| [11 — Human-in-the-loop](11-human-in-the-loop.md) | Why some actions must never run unsupervised, and two different ways to gate them |
| [12 — Evaluation](12-evaluation.md) | Why "it looked right in the demo" isn't evidence, and what to measure instead |
| [13 — Observability and cost](13-observability-and-cost.md) | Tracing a request across six services, and tokens as the unit of cost |
| [14 — Production concerns](14-production-concerns.md) | Idempotency, retries, rate limits — what turns a demo into a system (and an honest look at what this repo does and doesn't have yet) |

## How this fits together with the rest of the repo

Four layers, each answering a different question:

```
docs/concepts/       "What is this idea, and why would I reach for it?"
tutorials/NN         "Show me — I'll build a tiny version myself."
agents/python/       "Here it is doing real work, at scale, in production code."
docs/architecture.md "How does the whole system fit together?"
```

Nothing here is a summary of a blog post. If a concept page and the code it points to ever
disagree, the code is right and the page is out of date — please open an issue.
