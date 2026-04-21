# Chapter 16 — Magentic Orchestration

> **Post:** [https://nitinksingh.com/posts/maf-v1-16-magentic-orchestration/](https://nitinksingh.com/posts/maf-v1-16-magentic-orchestration/) — concept, diagrams, walkthrough.

A manager agent maintains a facts ledger and a plan, delegates to workers, reassesses after every turn, and replans when it stalls. The most autonomous orchestration in MAF — and the one with the most moving parts.

## Run the demos

**Prerequisites:** completed [Chapter 15 — Group Chat Orchestration](../15-group-chat-orchestration/).

**Environment variables** — repo-root `.env` with one LLM provider:

| Provider | Required | Optional |
|----------|----------|----------|
| **OpenAI** | `OPENAI_API_KEY` | `LLM_MODEL` (default `gpt-4.1`) |
| **Azure OpenAI** | `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_KEY`, `AZURE_OPENAI_DEPLOYMENT` | `AZURE_OPENAI_API_VERSION` (default `2024-10-21`) |

### Python

```bash
cd tutorials/16-magentic-orchestration/python
uv sync
uv run python main.py
uv run pytest -v
```

### .NET

Magentic is **Python-only** today — the .NET side is a stub that documents the gap. When `Microsoft.Agents.AI.Workflows` ships Magentic support, this stub becomes a working example.

```bash
cd tutorials/16-magentic-orchestration/dotnet
dotnet run   # prints the "not yet supported in C#" note
```

## What's in this folder

- [`python/`](./python/) — Python example + tests (`StandardMagenticManager` + workers)
- [`dotnet/`](./dotnet/) — placeholder pending C# Magentic support

## Learn more

- **Full article:** [maf-v1-16-magentic-orchestration](https://nitinksingh.com/posts/maf-v1-16-magentic-orchestration/)
- [Series index](../README.md) · Previous: [Ch15](../15-group-chat-orchestration/) · Next: [Ch17 Human-in-the-Loop](../17-human-in-the-loop/)
- Shared: [Mermaid style guide](../_shared/mermaid-style-guide.md) · [Jargon glossary](../_shared/jargon-glossary.md)
