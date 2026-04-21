# Chapter 04 — Sessions

> **Post:** [https://nitinksingh.com/posts/maf-v1-04-sessions/](https://nitinksingh.com/posts/maf-v1-04-sessions/) — concept, diagrams, walkthrough.

Serialize an AgentSession to JSON, persist it, reload in a fresh process, and have the agent pick up exactly where it left off.

## Run the demos

**Prerequisites:** completed [Chapter 03 — Streaming and Multi-turn](../03-streaming-and-multiturn/).

**Environment variables** — repo-root `.env` with one LLM provider:

| Provider | Required | Optional |
|----------|----------|----------|
| **OpenAI** | `OPENAI_API_KEY` | `LLM_MODEL` (default `gpt-4.1`) |
| **Azure OpenAI** | `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_KEY`, `AZURE_OPENAI_DEPLOYMENT` | `AZURE_OPENAI_API_VERSION` (default `2024-10-21`) |

### Python

```bash
cd tutorials/04-sessions/python
uv sync
uv run python main.py
uv run pytest -v
```

### .NET

```bash
cd tutorials/04-sessions/dotnet
dotnet run
dotnet test
```

## What's in this folder

- [`python/`](./python/) — Python example + tests
- [`dotnet/`](./dotnet/) — .NET example + tests

## Learn more

- **Full article:** [maf-v1-04-sessions](https://nitinksingh.com/posts/maf-v1-04-sessions/)
- [Series index](../README.md) · Previous: [Ch03](../03-streaming-and-multiturn/) · Next: [Ch05 Context Providers](../05-context-providers/)
- Shared: [Mermaid style guide](../_shared/mermaid-style-guide.md) · [Jargon glossary](../_shared/jargon-glossary.md)
