# Chapter 11 — Agents in Workflows

> **Post:** [https://nitinksingh.com/posts/maf-v1-11-agents-in-workflows/](https://nitinksingh.com/posts/maf-v1-11-agents-in-workflows/) — concept, diagrams, walkthrough.

LLM reasoning as a step in a deterministic pipeline: wrap a ChatClientAgent as an executor, chain two translators end-to-end, and see how the framework hides the adapter plumbing behind AgentWorkflowBuilder.

## Run the demos

**Prerequisites:** completed [Chapter 10 — Workflow Events](../10-workflow-events-and-builder/).

**Environment variables** — repo-root `.env` with one LLM provider:

| Provider | Required | Optional |
|----------|----------|----------|
| **OpenAI** | `OPENAI_API_KEY` | `LLM_MODEL` (default `gpt-4.1`) |
| **Azure OpenAI** | `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_KEY`, `AZURE_OPENAI_DEPLOYMENT` | `AZURE_OPENAI_API_VERSION` (default `2024-10-21`) |

### Python

```bash
cd tutorials/11-agents-in-workflows/python
uv sync
uv run python main.py
uv run pytest -v
```

### .NET

Two patterns, same demo:

```bash
cd tutorials/11-agents-in-workflows/dotnet
dotnet build
dotnet run -- --sequential "Hello, how are you?"   # convenience builder
dotnet run -- --manual     "Hello, how are you?"   # manual adapter pattern
dotnet test
```

## What's in this folder

- [`python/`](./python/) — Python example + tests
- [`dotnet/`](./dotnet/) — .NET example + tests (both `AgentWorkflowBuilder.BuildSequential` and manual `[MessageHandler]` adapters)

## Learn more

- **Full article:** [maf-v1-11-agents-in-workflows](https://nitinksingh.com/posts/maf-v1-11-agents-in-workflows/)
- [Series index](../README.md) · Previous: [Ch10](../10-workflow-events-and-builder/) · Next: [Ch12 Sequential Orchestration](../12-sequential-orchestration/)
- Shared: [Mermaid style guide](../_shared/mermaid-style-guide.md) · [Jargon glossary](../_shared/jargon-glossary.md)
