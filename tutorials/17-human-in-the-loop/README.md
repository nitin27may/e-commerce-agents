# Chapter 17 — Human-in-the-Loop

> **Post:** [https://nitinksingh.com/posts/maf-v1-17-human-in-the-loop/](https://nitinksingh.com/posts/maf-v1-17-human-in-the-loop/) — concept, diagrams, walkthrough.

Pause a workflow mid-run, ask a human, resume with their answer. Two calls to `workflow.run()`, one `request_id` tying them together — the caller's perspective is the whole story.

## Run the demos

**Prerequisites:** completed [Chapter 16 — Magentic Orchestration](../16-magentic-orchestration/).

**Environment variables** — repo-root `.env` with one LLM provider:

| Provider | Required | Optional |
|----------|----------|----------|
| **OpenAI** | `OPENAI_API_KEY` | `LLM_MODEL` (default `gpt-4.1`) |
| **Azure OpenAI** | `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_KEY`, `AZURE_OPENAI_DEPLOYMENT` | `AZURE_OPENAI_API_VERSION` (default `2024-10-21`) |

### Python

```bash
cd tutorials/17-human-in-the-loop/python
uv sync
uv run python main.py
uv run pytest -v
```

### .NET

```bash
cd tutorials/17-human-in-the-loop/dotnet
dotnet run -- 7   # pass an integer guess as arg
dotnet test
```

## What's in this folder

- [`python/`](./python/) — Python example + tests (`ctx.request_info` + `@response_handler`)
- [`dotnet/`](./dotnet/) — .NET example (`RequestPort.Create<,>` + `SendResponseAsync`)

## Learn more

- **Full article:** [maf-v1-17-human-in-the-loop](https://nitinksingh.com/posts/maf-v1-17-human-in-the-loop/)
- [Series index](../README.md) · Previous: [Ch16](../16-magentic-orchestration/) · Next: [Ch18 State and Checkpoints](../18-state-and-checkpoints/)
- Shared: [Mermaid style guide](../_shared/mermaid-style-guide.md) · [Jargon glossary](../_shared/jargon-glossary.md)
