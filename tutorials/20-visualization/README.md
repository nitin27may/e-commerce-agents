# Chapter 20 — Workflow Visualization

> **Post:** [https://nitinksingh.com/posts/maf-v1-20-visualization/](https://nitinksingh.com/posts/maf-v1-20-visualization/) — concept, diagrams, walkthrough.

Render any workflow as Mermaid (GitHub-friendly) or Graphviz DOT (production runbooks). One line each, deterministic output, dark-mode-safe palette.

## Run the demos

**Prerequisites:** completed [Chapter 19 — Declarative Workflows](../19-declarative-workflows/).

**Environment variables:** none — this chapter renders the graph; no LLM calls.

**Optional:** `graphviz` installed locally if you want to rasterize `.dot` to PNG/SVG via the `dot` CLI.

### Python

Run from the repo root using the shared `tutorials/` uv project (one `uv sync` covers every chapter):

```bash
uv sync --project tutorials
uv run --project tutorials python tutorials/20-visualization/python/main.py         # writes workflow.mmd + workflow.dot
uv run --project tutorials pytest tutorials/20-visualization/python/tests -v              # 9 tests, including determinism
```

### .NET

```bash
cd tutorials/20-visualization/dotnet
dotnet run
dotnet test
```

## What's in this folder

- [`python/`](./python/) — Python example + tests + sample output (`workflow.mmd`, `workflow.dot`)
- [`dotnet/`](./dotnet/) — .NET example + tests (`ToMermaidString` / `ToDotString`)

## Learn more

- **Full article:** [maf-v1-20-visualization](https://nitinksingh.com/posts/maf-v1-20-visualization/)
- [Series index](../README.md) · Previous: [Ch19](../19-declarative-workflows/) · Next: [Ch20b DevUI](../20b-devui/)
- Shared: [Mermaid style guide](../_shared/mermaid-style-guide.md) · [Jargon glossary](../_shared/jargon-glossary.md)
