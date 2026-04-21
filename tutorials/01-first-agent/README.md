# Chapter 01 — Your First Agent

> **Post:** [https://nitinksingh.com/posts/maf-v1-01-first-agent/](https://nitinksingh.com/posts/maf-v1-01-first-agent/) — concept, diagrams, walkthrough.

The smallest useful Microsoft Agent Framework program — 40 lines of code, one LLM call, in both Python and .NET.

## Run the demos

**Prerequisites:** completed [Chapter 00 — Setup](../00-setup/) (uv, .NET 9, Docker).

**Environment variables** — repo-root `.env` with one LLM provider:

| Provider | Required | Optional |
|----------|----------|----------|
| **OpenAI** | `OPENAI_API_KEY` | `LLM_MODEL` (default `gpt-4.1`) |
| **Azure OpenAI** | `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_KEY`, `AZURE_OPENAI_DEPLOYMENT` | `AZURE_OPENAI_API_VERSION` (default `2024-10-21`) |

### Python

```bash
cd tutorials/01-first-agent/python
uv sync
uv run python main.py
uv run pytest -v
```

### .NET

```bash
cd tutorials/01-first-agent/dotnet
dotnet run
dotnet test
```

Both produce: `A: The capital of France is Paris.`

## What's in this folder

- [`python/`](./python/) — Python example + tests
- [`dotnet/`](./dotnet/) — .NET example + tests

## Learn more

- **Full article:** [maf-v1-01-first-agent](https://nitinksingh.com/posts/maf-v1-01-first-agent/)
- [Series index](../README.md) · Previous: [Ch00 Setup](../00-setup/) · Next: [Ch02 Adding Tools](../02-add-tools/)
- Shared: [Mermaid style guide](../_shared/mermaid-style-guide.md) · [Jargon glossary](../_shared/jargon-glossary.md)
