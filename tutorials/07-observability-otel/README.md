# Chapter 07 — Observability with OpenTelemetry

> **Post:** [https://nitinksingh.com/posts/maf-v1-07-observability-otel/](https://nitinksingh.com/posts/maf-v1-07-observability-otel/) — concept, diagrams, walkthrough.

Wire OpenTelemetry to capture agent runs as spans with GenAI semantic attributes. Console for dev, OTLP for Aspire / Jaeger / Azure Monitor in prod — both stacks.

## Run the demos

**Prerequisites:** completed [Chapter 06 — Middleware](../06-middleware/).

**Environment variables** — repo-root `.env` with one LLM provider:

| Provider | Required | Optional |
|----------|----------|----------|
| **OpenAI** | `OPENAI_API_KEY` | `LLM_MODEL` (default `gpt-4.1`) |
| **Azure OpenAI** | `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_KEY`, `AZURE_OPENAI_DEPLOYMENT` | `AZURE_OPENAI_API_VERSION` (default `2024-10-21`) |

Optional (for OTLP export): `OTEL_EXPORTER_OTLP_ENDPOINT` (default `http://localhost:4317`). The Aspire Dashboard at `http://localhost:18888` is started by `./scripts/dev.sh`.

### Python

```bash
cd tutorials/07-observability-otel/python
uv sync
uv run python main.py
uv run pytest -v
```

### .NET

```bash
cd tutorials/07-observability-otel/dotnet
dotnet run
dotnet test
```

## What's in this folder

- [`python/`](./python/) — Python example + tests
- [`dotnet/`](./dotnet/) — .NET example + tests

## Learn more

- **Full article:** [maf-v1-07-observability-otel](https://nitinksingh.com/posts/maf-v1-07-observability-otel/)
- [Series index](../README.md) · Previous: [Ch06](../06-middleware/) · Next: [Ch08 MCP Tools](../08-mcp-tools/)
- Shared: [Mermaid style guide](../_shared/mermaid-style-guide.md) · [Jargon glossary](../_shared/jargon-glossary.md)
