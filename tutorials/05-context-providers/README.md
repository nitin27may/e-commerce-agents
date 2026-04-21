# Chapter 05 — Context Providers

> **Post:** [https://nitinksingh.com/posts/maf-v1-05-context-providers/](https://nitinksingh.com/posts/maf-v1-05-context-providers/) — concept, diagrams, walkthrough.

The clean hook for per-request context. One provider per concern, composed before every LLM call, no prompt-string juggling — plus TextSearchProvider for drop-in RAG.

## Run the demos

**Prerequisites:** completed [Chapter 04 — Sessions](../04-sessions/).

**Environment variables** — repo-root `.env` with one LLM provider:

| Provider | Required | Optional |
|----------|----------|----------|
| **OpenAI** | `OPENAI_API_KEY` | `LLM_MODEL` (default `gpt-4.1`) |
| **Azure OpenAI** | `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_KEY`, `AZURE_OPENAI_DEPLOYMENT` | `AZURE_OPENAI_API_VERSION` (default `2024-10-21`) |

### Python

```bash
cd tutorials/05-context-providers/python
uv sync
uv run python main.py
uv run pytest -v
```

### .NET

```bash
cd tutorials/05-context-providers/dotnet
dotnet run
dotnet test
```

## What's in this folder

- [`python/`](./python/) — Python example + tests
- [`dotnet/`](./dotnet/) — .NET example + tests

## Learn more

- **Full article:** [maf-v1-05-context-providers](https://nitinksingh.com/posts/maf-v1-05-context-providers/)
- [Series index](../README.md) · Previous: [Ch04](../04-sessions/) · Next: [Ch06 Middleware](../06-middleware/)
- Shared: [Mermaid style guide](../_shared/mermaid-style-guide.md) · [Jargon glossary](../_shared/jargon-glossary.md)
