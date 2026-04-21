# Chapter 03 — Streaming and Multi-turn

> **Post:** [https://nitinksingh.com/posts/maf-v1-03-streaming-and-multiturn/](https://nitinksingh.com/posts/maf-v1-03-streaming-and-multiturn/) — concept, diagrams, walkthrough.

Stream tokens as they arrive and reuse a session across turns so the LLM sees the full conversation. ~60 lines of code per language.

## Run the demos

**Prerequisites:** completed [Chapter 02 — Adding Tools](../02-add-tools/).

**Environment variables** — repo-root `.env` with one LLM provider:

| Provider | Required | Optional |
|----------|----------|----------|
| **OpenAI** | `OPENAI_API_KEY` | `LLM_MODEL` (default `gpt-4.1`) |
| **Azure OpenAI** | `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_KEY`, `AZURE_OPENAI_DEPLOYMENT` | `AZURE_OPENAI_API_VERSION` (default `2024-10-21`) |

### Python

```bash
cd tutorials/03-streaming-and-multiturn/python
uv sync
uv run python main.py
uv run pytest -v
```

### .NET

```bash
cd tutorials/03-streaming-and-multiturn/dotnet
dotnet run
dotnet test
```

## What's in this folder

- [`python/`](./python/) — Python example + tests
- [`dotnet/`](./dotnet/) — .NET example + tests

## Learn more

- **Full article:** [maf-v1-03-streaming-and-multiturn](https://nitinksingh.com/posts/maf-v1-03-streaming-and-multiturn/)
- [Series index](../README.md) · Previous: [Ch02](../02-add-tools/) · Next: [Ch04 Sessions](../04-sessions/)
- Shared: [Mermaid style guide](../_shared/mermaid-style-guide.md) · [Jargon glossary](../_shared/jargon-glossary.md)
