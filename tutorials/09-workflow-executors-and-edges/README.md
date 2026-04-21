# Chapter 09 — Workflow Executors and Edges

> **Post:** [https://nitinksingh.com/posts/maf-v1-09-workflow-executors-and-edges/](https://nitinksingh.com/posts/maf-v1-09-workflow-executors-and-edges/) — concept, diagrams, walkthrough.

Step beyond single-agent runs into deterministic orchestration. Executors are the nodes, edges are the routes, and the whole thing runs on a Pregel-style superstep scheduler. Same concept, same behaviour in Python and .NET.

## Run the demos

**Prerequisites:** completed [Chapter 08 — MCP Tools](../08-mcp-tools/).

**Environment variables:** none. This chapter runs pure string-transformation executors — no LLM calls.

### Python

```bash
cd tutorials/09-workflow-executors-and-edges/python
uv sync
uv run python main.py
uv run pytest -v
```

### .NET

```bash
cd tutorials/09-workflow-executors-and-edges/dotnet
dotnet run
dotnet test
```

## What's in this folder

- [`python/`](./python/) — Python example + tests
- [`dotnet/`](./dotnet/) — .NET example + tests (full source-generator example with `[MessageHandler]`)

## Learn more

- **Full article:** [maf-v1-09-workflow-executors-and-edges](https://nitinksingh.com/posts/maf-v1-09-workflow-executors-and-edges/)
- [Series index](../README.md) · Previous: [Ch08](../08-mcp-tools/) · Next: [Ch10 Workflow Events](../10-workflow-events-and-builder/)
- Shared: [Mermaid style guide](../_shared/mermaid-style-guide.md) · [Jargon glossary](../_shared/jargon-glossary.md)
