# Chapter 19 — Declarative Workflows

> **Post:** [https://nitinksingh.com/posts/maf-v1-19-declarative-workflows/](https://nitinksingh.com/posts/maf-v1-19-declarative-workflows/) — concept, diagrams, walkthrough.

Define a workflow in YAML and load it at runtime. Config-driven orchestration — no recompile to tweak the graph.

## Run the demos

**Prerequisites:** completed [Chapter 18 — State and Checkpoints](../18-state-and-checkpoints/).

**Environment variables:** none — this chapter uses built-in string-transformation ops, no LLM calls.

### Python

```bash
cd tutorials/19-declarative-workflows/python
uv sync
uv run python main.py "hello world"    # loads workflow.yaml
uv run pytest -v
```

### .NET

```bash
cd tutorials/19-declarative-workflows/dotnet
dotnet run -- "hello world"
dotnet test
```

## What's in this folder

- [`python/`](./python/) — Python example + tests + `workflow.yaml`
- [`dotnet/`](./dotnet/) — .NET example + tests (custom `DeclarativeExecutor` with source generator)

## Learn more

- **Full article:** [maf-v1-19-declarative-workflows](https://nitinksingh.com/posts/maf-v1-19-declarative-workflows/)
- [Series index](../README.md) · Previous: [Ch18](../18-state-and-checkpoints/) · Next: [Ch20 Visualization](../20-visualization/)
- Shared: [Mermaid style guide](../_shared/mermaid-style-guide.md) · [Jargon glossary](../_shared/jargon-glossary.md)
