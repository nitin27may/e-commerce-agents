---
title: "MAF v1 — MCP Tools (Python + .NET)"
date: 2026-04-21
lastmod: 2026-04-21
draft: true
tags: [microsoft-agent-framework, ai-agents, python, dotnet, mcp, model-context-protocol, tutorial]
categories: [Deep Dive]
series: ["MAF v1: Python and .NET"]
summary: "Plug a Model Context Protocol (MCP) server into an agent — the same weather server powers both the Python and .NET clients."
cover:
  image: "img/posts/maf-v1-mcp.jpg"
  alt: "Agent calling tools over the Model Context Protocol"
author: "Nitin Kumar Singh"
toc: true
---

> **Series note** — Supersedes [Part 10 — MCP Integration](https://nitinksingh.com/posts/mcp-integration--connecting-ai-agents-to-the-tool-ecosystem/). That article explains *why* MCP exists (tool interop across frameworks); this one is the minimum code on both sides.

## Why this chapter

MCP is the tool-level equivalent of USB: one server exposes capabilities that any MCP-speaking agent can consume. You write the tool once and every framework (MAF, LangChain, Claude Desktop, Cursor) can use it.

In this chapter we stand up a tiny Python MCP server with a single `get_weather` tool, then call it from **both** a Python MAF agent and a .NET MAF agent. Same server, two clients.

## Prerequisites

- Completed [Chapter 07 — Observability](../07-observability-otel/)
- `.env` with working credentials
- `mcp` Python package installed (`uv sync` in `agents/` pulls it in)

## The concept

MCP defines a JSON-RPC protocol over three transports: **stdio**, **HTTP/SSE**, and **Streamable HTTP**. This chapter uses stdio — the MAF client spawns the server as a subprocess and communicates over its stdin/stdout.

MAF hides the protocol details:

- **Python**: `MCPStdioTool(name, command, args=[...])` — used as an async context manager; passed to `Agent(..., tools=[mcp])`.
- **.NET**: `McpClient.CreateAsync(StdioClientTransport)` → `ListToolsAsync()` returns `McpClientTool[]`; each is an `AITool` and goes straight to `.AsAIAgent(tools: ...)`.

Both flavors auto-discover tools at connection time, so your agent code doesn't hard-code the tool list.

## The server

Source: [`python/weather_mcp_server.py`](./python/weather_mcp_server.py). Twelve lines of `FastMCP`:

```python
from mcp.server.fastmcp import FastMCP

server = FastMCP("maf-v1-ch08-weather")

@server.tool()
def get_weather(city: str) -> str:
    """Look up the current weather for a city (canned data)."""
    canned = {"paris": "Sunny, 18°C.", "london": "Overcast, 12°C.", "tokyo": "Rain, 15°C."}
    return canned.get(city.lower(), f"No weather data for {city}.")

if __name__ == "__main__":
    server.run()
```

## Python client

Source: [`python/main.py`](./python/main.py).

```python
from agent_framework import Agent
from agent_framework._mcp import MCPStdioTool

SERVER_SCRIPT = str(pathlib.Path(__file__).parent / "weather_mcp_server.py")

async def run(question: str) -> str:
    mcp = MCPStdioTool(name="weather-mcp", command=sys.executable, args=[SERVER_SCRIPT])
    async with mcp:
        agent = Agent(client, instructions="...", tools=[mcp])
        return (await agent.run(question)).text
```

The `async with` handshake spawns the subprocess, performs the MCP init, and lists tools. When the block exits, the subprocess is terminated.

## .NET client

Source: [`dotnet/Program.cs`](./dotnet/Program.cs).

```csharp
using ModelContextProtocol.Client;

var transport = new StdioClientTransport(new StdioClientTransportOptions
{
    Name = "weather-mcp",
    Command = "/path/to/python",
    Arguments = new[] { ServerScript },
});

await using var mcpClient = await McpClient.CreateAsync(transport);
var tools = (await mcpClient.ListToolsAsync()).Select(t => (AITool)t).ToArray();

var agent = chatClient.AsAIAgent(instructions: "...", name: "mcp-agent", tools: tools);
var response = await agent.RunAsync(question);
```

The `.NET` MCP SDK (`ModelContextProtocol.Client`) produces `McpClientTool` objects that implement `AITool` directly — no adapter needed.

## Side-by-side differences

| Aspect | Python | .NET |
|--------|--------|------|
| Package | `mcp` (server) + MAF's `_mcp` module | `ModelContextProtocol` + `ModelContextProtocol.Core` |
| Client class | `MCPStdioTool` (async context manager) | `McpClient` + `StdioClientTransport` |
| Tool discovery | Implicit when entering `async with` | Explicit `ListToolsAsync()` |
| Lifecycle | `async with` handles setup/teardown | `await using` on the client |

Both honor the same MCP spec, so either client works against either-language servers.

## Gotchas

- **The subprocess needs the same Python env as the server imports.** In our example the .NET test sets `PYTHON_BIN` (defaults to the repo's shared venv).
- **Long-running MCP servers** keep the subprocess alive between runs. Wrap them in `async with` (Python) or `await using` (.NET) to avoid orphan processes.
- **Tool names can collide** across multiple MCP servers. Pass `tool_name_prefix=` (Python) to namespace them.
- **Approval modes** — MAF supports `approval_mode="always_require"` so sensitive MCP tools pause for a user confirmation (covered in Ch17).

## Tests

```bash
# Python: 3 unit + 2 real-LLM integration
source agents/.venv/bin/activate
python -m pytest tutorials/08-mcp-tools/python/tests/ -v

# .NET: 3 real-LLM integration (uses the Python server over stdio)
cd tutorials/08-mcp-tools/dotnet
dotnet test tests/McpTools.Tests.csproj
```

All 8 tests green. Both language agents successfully call the same canned Python MCP server against live Azure OpenAI.

## How this shows up in the capstone

- `agents/mcp/inventory_server.py` is a real MCP server exposing warehouse + shipping tools that any framework could consume. The capstone currently calls it over a direct HTTP handshake; Phase 7 `plans/refactor/` item wires it into specialists via MAF's MCP client so tools are auto-discovered.
- MCP vs A2A: MCP is about tools (stateless calls, open catalog); A2A is about agents (stateful conversations, specialist routing). They compose — a specialist agent can be an A2A endpoint that internally consumes MCP tools.

## What's next

- Next chapter: [Chapter 09 — Workflow Executors and Edges](../09-workflow-executors-and-edges/)
- Full source: [`python/`](./python/) · [`dotnet/`](./dotnet/)
- [MAF docs — Hosted MCP Tools](https://learn.microsoft.com/en-us/agent-framework/agents/tools/hosted-mcp-tools/)
