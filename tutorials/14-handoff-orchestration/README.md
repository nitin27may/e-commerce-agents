# Chapter 14 — Handoff Orchestration

> **Post:** [https://nitinksingh.com/posts/maf-v1-14-handoff-orchestration/](https://nitinksingh.com/posts/maf-v1-14-handoff-orchestration/) — concept, diagrams, walkthrough.

Agents decide where the conversation goes next. A Triage agent emits a synthesised `handoff_to_<name>` tool call and control transfers to a Math or History specialist, which can hand back. Mesh topology, turn limits, and why loops are the failure mode you actually see in production.

## Run the demos

**Prerequisites:** completed [Chapter 13 — Concurrent Orchestration](../13-concurrent-orchestration/).

**Environment variables** — repo-root `.env` with one LLM provider:

| Provider | Required | Optional |
|----------|----------|----------|
| **OpenAI** | `OPENAI_API_KEY` | `LLM_MODEL` (default `gpt-4.1`) |
| **Azure OpenAI** | `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_KEY`, `AZURE_OPENAI_DEPLOYMENT` | `AZURE_OPENAI_API_VERSION` (default `2024-10-21`) |

### Python

```bash
cd tutorials/14-handoff-orchestration/python
uv sync
uv run python main.py
uv run pytest -v
```

### .NET

```bash
cd tutorials/14-handoff-orchestration/dotnet
dotnet run
dotnet test
```

## What's in this folder

- [`python/`](./python/) — Python example + tests
- [`dotnet/`](./dotnet/) — .NET example + tests (`CreateHandoffBuilderWith(...).WithHandoffs(...)`)

## Learn more

- **Full article:** [maf-v1-14-handoff-orchestration](https://nitinksingh.com/posts/maf-v1-14-handoff-orchestration/)
- [Series index](../README.md) · Previous: [Ch13](../13-concurrent-orchestration/) · Next: [Ch15 Group Chat Orchestration](../15-group-chat-orchestration/)
- Shared: [Mermaid style guide](../_shared/mermaid-style-guide.md) · [Jargon glossary](../_shared/jargon-glossary.md)
