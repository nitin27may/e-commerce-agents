# Chapter 08 — MCP Tools

## Goal

Use a hosted MCP server and stand up a local one. Show when MCP wins over native `@tool` and over A2A.

## Article mapping

- **Supersedes**: [Part 10 — MCP Integration](https://nitinksingh.com/posts/mcp-integration--connecting-ai-agents-to-the-tool-ecosystem/)
- **New slug**: `/posts/maf-v1-mcp/`

## Teaching strategy

- [x] Partial refactor — `agents/mcp/inventory_server.py:42` already exposes a local MCP server; this chapter wires an agent to it using MAF's MCP client.

## Deliverables

### `python/`
- `main.py` — agent configured with a hosted MCP tool (e.g., DeepWiki) and a local one (inventory). Demonstrates both discovery and invocation.
- `tests/test_mcp.py` — ≥ 3 tests using a fake MCP server: tool discovery, invocation, error on unreachable server.

### `dotnet/`
- Equivalent using `MCPToolDefinition` + `MCPToolResource`.

### Article
- Decision matrix: MCP vs `@tool` vs A2A.
- Security considerations (tool approval, server allowlists).

## Verification

- The agent calls a canned MCP tool and the response flows back through the usual message loop.

## How this maps into the capstone

`agents/mcp/inventory_server.py` remains a standalone MCP server. Phase 7 adds an orchestrator path that consumes it via MAF MCP client, reusing the same base URL map as A2A.

## Out of scope

- Running an MCP server in production (auth, rate limits).
- Tool approval — brief mention, full coverage in Ch17 (HITL).
