# Chapter 13 — Concurrent Orchestration

> **Post:** [https://nitinksingh.com/posts/maf-v1-13-concurrent-orchestration/](https://nitinksingh.com/posts/maf-v1-13-concurrent-orchestration/) — concept, diagrams, walkthrough.

Three agents, one input, parallel LLM calls. Fan-out to an expert panel, fan-in through an aggregator, wall-clock time bounded by the slowest branch instead of their sum.

## Run the demos

**Prerequisites:** completed [Chapter 12 — Sequential Orchestration](../12-sequential-orchestration/).

**Environment variables** — repo-root `.env` with one LLM provider:

| Provider | Required | Optional |
|----------|----------|----------|
| **OpenAI** | `OPENAI_API_KEY` | `LLM_MODEL` (default `gpt-4.1`) |
| **Azure OpenAI** | `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_KEY`, `AZURE_OPENAI_DEPLOYMENT` | `AZURE_OPENAI_API_VERSION` (default `2024-10-21`) |

### Python

```bash
cd tutorials/13-concurrent-orchestration/python
uv sync
uv run python main.py
uv run pytest -v
```

### .NET

```bash
cd tutorials/13-concurrent-orchestration/dotnet
dotnet run
dotnet test
```

## What's in this folder

- [`python/`](./python/) — Python example + tests
- [`dotnet/`](./dotnet/) — .NET example + tests (`AgentWorkflowBuilder.BuildConcurrent` with aggregator)

## Learn more

- **Full article:** [maf-v1-13-concurrent-orchestration](https://nitinksingh.com/posts/maf-v1-13-concurrent-orchestration/)
- [Series index](../README.md) · Previous: [Ch12](../12-sequential-orchestration/) · Next: [Ch14 Handoff Orchestration](../14-handoff-orchestration/)
- Shared: [Mermaid style guide](../_shared/mermaid-style-guide.md) · [Jargon glossary](../_shared/jargon-glossary.md)
