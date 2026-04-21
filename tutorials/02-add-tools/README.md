# Chapter 02 — Adding Tools

> **Post:** [https://nitinksingh.com/posts/maf-v1-02-add-tools/](https://nitinksingh.com/posts/maf-v1-02-add-tools/) — concept, diagrams, walkthrough.

Give an agent a function — the LLM decides when to call it, MAF runs the loop. One canned-data example, Python and .NET side by side, plus structured outputs and the hosted-tools catalogue.

## Run the demos

**Prerequisites:** completed [Chapter 01 — Your First Agent](../01-first-agent/).

**Environment variables** — repo-root `.env` with one LLM provider:

| Provider | Required | Optional |
|----------|----------|----------|
| **OpenAI** | `OPENAI_API_KEY` | `LLM_MODEL` (default `gpt-4.1`) |
| **Azure OpenAI** | `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_KEY`, `AZURE_OPENAI_DEPLOYMENT` | `AZURE_OPENAI_API_VERSION` (default `2024-10-21`) |

### Python

```bash
cd tutorials/02-add-tools/python
uv sync
uv run python main.py
uv run pytest -v
```

### .NET

```bash
cd tutorials/02-add-tools/dotnet
dotnet run
dotnet test
```

## What's in this folder

- [`python/`](./python/) — Python example + tests
- [`dotnet/`](./dotnet/) — .NET example + tests

## Learn more

- **Full article:** [maf-v1-02-add-tools](https://nitinksingh.com/posts/maf-v1-02-add-tools/)
- [Series index](../README.md) · Previous: [Ch01](../01-first-agent/) · Next: [Ch03 Streaming + Multi-turn](../03-streaming-and-multiturn/)
- Shared: [Mermaid style guide](../_shared/mermaid-style-guide.md) · [Jargon glossary](../_shared/jargon-glossary.md)
