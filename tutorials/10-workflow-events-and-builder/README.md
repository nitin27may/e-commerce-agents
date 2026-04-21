# Chapter 10 — Workflow Events and Builder

> **Post:** [https://nitinksingh.com/posts/maf-v1-10-workflow-events-and-builder/](https://nitinksingh.com/posts/maf-v1-10-workflow-events-and-builder/) — concept, diagrams, walkthrough.

Two kinds of workflow events — lifecycle and custom. Subscribe to the stream in Python and .NET, filter by type, and wire a live progress indicator.

## Run the demos

**Prerequisites:** completed [Chapter 09 — Workflow Executors and Edges](../09-workflow-executors-and-edges/).

**Environment variables:** none. This chapter runs pure string-transformation executors — no LLM calls.

### Python

```bash
cd tutorials/10-workflow-events-and-builder/python
uv sync
uv run python main.py
uv run pytest -v
```

### .NET

```bash
cd tutorials/10-workflow-events-and-builder/dotnet
dotnet run
dotnet test
```

## What's in this folder

- [`python/`](./python/) — Python example + tests
- [`dotnet/`](./dotnet/) — .NET example + tests (full source-generator example emitting `WorkflowEvent` subclasses)

## Learn more

- **Full article:** [maf-v1-10-workflow-events-and-builder](https://nitinksingh.com/posts/maf-v1-10-workflow-events-and-builder/)
- [Series index](../README.md) · Previous: [Ch09](../09-workflow-executors-and-edges/) · Next: [Ch11 Agents in Workflows](../11-agents-in-workflows/)
- Shared: [Mermaid style guide](../_shared/mermaid-style-guide.md) · [Jargon glossary](../_shared/jargon-glossary.md)
