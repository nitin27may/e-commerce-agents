# Chapter 20b — DevUI: interactive dashboard for agents and workflows

> **Post:** [https://nitinksingh.com/posts/maf-v1-20b-devui/](https://nitinksingh.com/posts/maf-v1-20b-devui/) — concept, diagrams, walkthrough.

DevUI is MAF's dev-only browser harness: type a prompt, watch tool calls fire, inspect OTel spans in real time. Directory discovery or programmatic registration, OpenAI-compatible Responses API on localhost. **Python-only today, C# coming soon.**

## Run the demo

**Prerequisites:** completed [Chapter 20 — Visualization](../20-visualization/).

**Environment variables** — repo-root `.env` with one LLM provider:

| Provider | Required | Optional |
|----------|----------|----------|
| **OpenAI** | `OPENAI_API_KEY` | `LLM_MODEL` (default `gpt-4.1`) |
| **Azure OpenAI** | `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_KEY`, `AZURE_OPENAI_DEPLOYMENT` | `AZURE_OPENAI_API_VERSION` (default `2024-10-21`) |

### Python (only)

```bash
cd tutorials/20b-devui/python
uv sync
uv run python main.py
# → DevUI opens at http://localhost:8090 (auto-opens browser)
uv run pytest -v
```

### .NET

DevUI C# is documented as "coming soon" by Microsoft. See [`dotnet/README.md`](./dotnet/README.md) for the tracking note and the upstream [DevUI docs](https://learn.microsoft.com/en-us/agent-framework/devui/).

## What's in this folder

- [`python/`](./python/) — Python example (`serve(entities=[agent])`) + 3 tests
- [`dotnet/`](./dotnet/README.md) — C# tracking stub

## Learn more

- **Full article:** [maf-v1-20b-devui](https://nitinksingh.com/posts/maf-v1-20b-devui/)
- [Series index](../README.md) · Previous: [Ch20](../20-visualization/) · Next: [Ch21 Capstone Tour](../21-capstone-tour/)
- Shared: [Mermaid style guide](../_shared/mermaid-style-guide.md) · [Jargon glossary](../_shared/jargon-glossary.md)
