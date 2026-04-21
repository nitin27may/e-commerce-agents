# Chapter 06 — Middleware and the Agent Pipeline

> **Post:** [https://nitinksingh.com/posts/maf-v1-06-middleware/](https://nitinksingh.com/posts/maf-v1-06-middleware/) — concept, diagrams, walkthrough.

Three layers, one composable pipeline. Wrap every agent run, intercept every tool call, redact PII before the LLM ever sees it — in both Python and .NET, with the same three abstractions.

## Run the demos

**Prerequisites:** completed [Chapter 05 — Context Providers](../05-context-providers/).

**Environment variables** — repo-root `.env` with one LLM provider:

| Provider | Required | Optional |
|----------|----------|----------|
| **OpenAI** | `OPENAI_API_KEY` | `LLM_MODEL` (default `gpt-4.1`) |
| **Azure OpenAI** | `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_KEY`, `AZURE_OPENAI_DEPLOYMENT` | `AZURE_OPENAI_API_VERSION` (default `2024-10-21`) |

### Python

```bash
cd tutorials/06-middleware/python
uv sync
uv run python main.py
uv run pytest -v
```

### .NET

```bash
cd tutorials/06-middleware/dotnet
dotnet run
dotnet test
```

## What's in this folder

- [`python/`](./python/) — Python example + tests
- [`dotnet/`](./dotnet/) — .NET example + tests

## Learn more

- **Full article:** [maf-v1-06-middleware](https://nitinksingh.com/posts/maf-v1-06-middleware/)
- [Series index](../README.md) · Previous: [Ch05](../05-context-providers/) · Next: [Ch07 Observability](../07-observability-otel/)
- Shared: [Mermaid style guide](../_shared/mermaid-style-guide.md) · [Jargon glossary](../_shared/jargon-glossary.md)
