# Chapter 12 — Sequential Orchestration

> **Post:** [https://nitinksingh.com/posts/maf-v1-12-sequential-orchestration/](https://nitinksingh.com/posts/maf-v1-12-sequential-orchestration/) — concept, diagrams, walkthrough.

The assembly-line pattern for agents. `SequentialBuilder` / `BuildSequential` hides the adapters you wrote by hand in Chapter 11 and forwards the shared conversation down a Writer → Reviewer → Finalizer pipeline.

## Run the demos

**Prerequisites:** completed [Chapter 11 — Agents in Workflows](../11-agents-in-workflows/).

**Environment variables** — repo-root `.env` with one LLM provider:

| Provider | Required | Optional |
|----------|----------|----------|
| **OpenAI** | `OPENAI_API_KEY` | `LLM_MODEL` (default `gpt-4.1`) |
| **Azure OpenAI** | `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_KEY`, `AZURE_OPENAI_DEPLOYMENT` | `AZURE_OPENAI_API_VERSION` (default `2024-10-21`) |

### Python

```bash
cd tutorials/12-sequential-orchestration/python
uv sync
uv run python main.py
uv run pytest -v
```

### .NET

```bash
cd tutorials/12-sequential-orchestration/dotnet
dotnet run
dotnet test
```

## What's in this folder

- [`python/`](./python/) — Python example + tests
- [`dotnet/`](./dotnet/) — .NET example + tests (`AgentWorkflowBuilder.BuildSequential`)

## Learn more

- **Full article:** [maf-v1-12-sequential-orchestration](https://nitinksingh.com/posts/maf-v1-12-sequential-orchestration/)
- [Series index](../README.md) · Previous: [Ch11](../11-agents-in-workflows/) · Next: [Ch13 Concurrent Orchestration](../13-concurrent-orchestration/)
- Shared: [Mermaid style guide](../_shared/mermaid-style-guide.md) · [Jargon glossary](../_shared/jargon-glossary.md)
