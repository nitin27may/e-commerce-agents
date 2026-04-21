# Chapter 15 — Group Chat Orchestration

> **Post:** [https://nitinksingh.com/posts/maf-v1-15-group-chat-orchestration/](https://nitinksingh.com/posts/maf-v1-15-group-chat-orchestration/) — concept, diagrams, walkthrough.

A centralized manager picks who speaks next each round. Round-robin, prompt-driven, and agent-driven strategies for the same Writer/Critic/Editor loop, plus the knobs that keep the loop from running forever.

## Run the demos

**Prerequisites:** completed [Chapter 14 — Handoff Orchestration](../14-handoff-orchestration/).

**Environment variables** — repo-root `.env` with one LLM provider:

| Provider | Required | Optional |
|----------|----------|----------|
| **OpenAI** | `OPENAI_API_KEY` | `LLM_MODEL` (default `gpt-4.1`) |
| **Azure OpenAI** | `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_KEY`, `AZURE_OPENAI_DEPLOYMENT` | `AZURE_OPENAI_API_VERSION` (default `2024-10-21`) |

### Python

Two manager strategies supported via CLI arg:

```bash
cd tutorials/15-group-chat-orchestration/python
uv sync
uv run python main.py "slogan for a coffee shop"          # round-robin
uv run python main.py "slogan for a coffee shop" prompt   # prompt-driven
uv run pytest -v
```

### .NET

```bash
cd tutorials/15-group-chat-orchestration/dotnet
dotnet run -- "slogan for a bookstore"          # round-robin
dotnet run -- "slogan for a bookstore" prompt   # prompt-driven
dotnet test
```

## What's in this folder

- [`python/`](./python/) — Python example + tests (round-robin + prompt-driven + agent-driven)
- [`dotnet/`](./dotnet/) — .NET example (`RoundRobinGroupChatManager` + subclassed `GroupChatManager`)

## Learn more

- **Full article:** [maf-v1-15-group-chat-orchestration](https://nitinksingh.com/posts/maf-v1-15-group-chat-orchestration/)
- [Series index](../README.md) · Previous: [Ch14](../14-handoff-orchestration/) · Next: [Ch16 Magentic Orchestration](../16-magentic-orchestration/)
- Shared: [Mermaid style guide](../_shared/mermaid-style-guide.md) · [Jargon glossary](../_shared/jargon-glossary.md)
