# Chapter 08 — MCP Tools

> **Post:** [https://nitinksingh.com/posts/maf-v1-08-mcp-tools/](https://nitinksingh.com/posts/maf-v1-08-mcp-tools/) — concept, diagrams, walkthrough.

Stand up one Python MCP server and consume it from both a Python MAF agent and a .NET MAF agent. Discovery handshake on the wire, zero-copy AITool at the client.

## Run the demos

**Prerequisites:** completed [Chapter 07 — Observability](../07-observability-otel/).

**Environment variables** — repo-root `.env` with one LLM provider:

| Provider | Required | Optional |
|----------|----------|----------|
| **OpenAI** | `OPENAI_API_KEY` | `LLM_MODEL` (default `gpt-4.1`) |
| **Azure OpenAI** | `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_KEY`, `AZURE_OPENAI_DEPLOYMENT` | `AZURE_OPENAI_API_VERSION` (default `2024-10-21`) |

### Python client

```bash
cd tutorials/08-mcp-tools/python
uv sync
uv run python main.py
uv run pytest -v
```

### .NET client

```bash
cd tutorials/08-mcp-tools/dotnet
dotnet run
dotnet test
```

Both clients launch the MCP server as a stdio subprocess automatically.

## What's in this folder

- [`python/`](./python/) — Python server + client + tests
- [`dotnet/`](./dotnet/) — .NET client + tests

## Learn more

- **Full article:** [maf-v1-08-mcp-tools](https://nitinksingh.com/posts/maf-v1-08-mcp-tools/)
- [Series index](../README.md) · Previous: [Ch07](../07-observability-otel/) · Next: [Ch09 Workflow Executors](../09-workflow-executors-and-edges/)
- Shared: [Mermaid style guide](../_shared/mermaid-style-guide.md) · [Jargon glossary](../_shared/jargon-glossary.md)
