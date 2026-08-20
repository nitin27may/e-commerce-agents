# MCP Python SDK 2.0.0 Migration

**Status:** Blocked upstream — not started. **Depends on:** `agent-framework-core` lifting its own
`mcp<2` constraint. **Current pin:** `mcp[cli]>=1.27.0,<2` (latest compatible: `1.29.0`) across
`agents/python/pyproject.toml`, `agents/python/packages/mcp-product/pyproject.toml`,
`agents/python/packages/mcp-inventory/pyproject.toml`.

## Why this exists

During the Phase 0 dependency refresh (bumping `agent-framework-core`/`agent-framework-openai` to
1.14.0/1.13.0), `uv lock --upgrade` resolved `mcp[cli]>=1.27.0` up to `2.0.0`. That broke both MCP
server packages immediately: `mcp.server.fastmcp` no longer exists at that version. Rather than
silently pin around it, this doc records exactly what changed, why the migration is deferred, and
what it takes to complete once unblocked.

## What actually changed in `mcp` 2.0.0 (verified by installing it in isolation)

- **`FastMCP` is renamed to `MCPServer`.** The module moved from `mcp.server.fastmcp` to
  `mcp.server.mcpserver`. No backward-compatible alias exists — this is a hard rename, not a
  deprecation shim.
- **`MCPServer.__init__` is a superset of the old `FastMCP.__init__`** — `lifespan`,
  `token_verifier`, `streamable_http_app()` all still exist with the same names and shapes. The
  constructor adds new params (`resource_security`, `request_state_security`, `middleware`,
  `subscriptions`, `cache_hints`) with defaults that preserve current behavior
  (`resource_security` defaults to `reject_path_traversal=True` etc., which we already want).
- **`mcp.server.auth.provider.{AccessToken, TokenVerifier}` — unchanged import path.** Both
  `packages/mcp-product/src/ecommerce_mcp_product/auth.py` and the inventory equivalent import
  these directly; no change needed there.
- **The negotiated MCP protocol version advanced** from `2025-11-25` (in 1.29.0) to `2026-07-28`
  (in 2.0.0) — a real wire-protocol bump, not just a Python API rename.

## Why it's blocked, not just "more work"

`agent-framework-core` (the framework this entire repo is built on) declares its own dependency as
`mcp>=1.24.0,<2 ; extra == "all"` — verified via
`importlib.metadata.requires("agent-framework-core")`. That's not our constraint; it's Microsoft's.
`agent_framework/_mcp.py` (the module backing `MCPStreamableHTTPTool`, which is exactly how
`product_discovery/agent.py` and `inventory_fulfillment/agent.py` consume `mcp-product` and
`mcp-inventory` when `MCP_ENABLED=true`) imports the `mcp` package directly.

So the two MCP *servers* and the framework's MCP *client* are both bound by the same upstream
package, and the framework side is hard-capped below 2.0.0. Migrating only the servers would create
version skew between what the client library speaks and what the server expects — on a real
protocol-version bump, not a cosmetic one. That is a subtler and worse failure mode than the import
error we caught immediately: a mismatch here could surface as intermittent tool-call failures or
silent capability negotiation downgrades rather than a clean crash at startup.

**This migration is gated on `agent-framework-core` itself moving to `mcp>=2`.** Chasing it earlier
means migrating twice, or shipping a client/server version mismatch that MAF's own team hasn't
signed off on.

## What to do when unblocked

1. Confirm `agent-framework-core`'s `all` extra (or whatever `MCPStreamableHTTPTool` depends on by
   then) allows `mcp>=2`. Check via
   `python -c "import importlib.metadata as m; print(m.requires('agent-framework-core'))"` after
   bumping.
2. In both `packages/mcp-product/src/.../server.py` and
   `packages/mcp-inventory/src/.../server.py`: replace
   `from mcp.server.fastmcp import FastMCP` with `from mcp.server.mcpserver import MCPServer`, and
   every `FastMCP(...)` construction with `MCPServer(...)`. The constructor call sites
   (`_lifespan(server: FastMCP)` type hints, `mcp = FastMCP("product-discovery-mcp",
   **_mcp_kwargs)`) are mechanical renames — the kwargs shape is unchanged.
3. Re-verify the DNS-rebinding Host-header protection comment in both `server.py` files against the
   new `resource_security`/`request_state_security` params — confirm the new defaults still cover
   what the current comment describes, and update the comment if the mechanism moved.
4. Run both packages' test suites (`packages/mcp-product/tests/`, `packages/mcp-inventory/tests/`)
   plus `tests/test_mcp_oauth_integration.py` in `agents/python/tests/`.
5. Do an actual end-to-end smoke test with `MCP_ENABLED=true` against a running stack — start
   `mcp-product`/`mcp-inventory` via `docker compose --profile mcp --profile agents up`, and drive a
   real `product_discovery` chat query that exercises `MCPStreamableHTTPTool`, to catch any protocol
   negotiation issue that unit tests alone wouldn't surface.
6. Bump the pin in all three `pyproject.toml` files from `mcp[cli]>=1.27.0,<2` to a real floor on
   the 2.x line, and drop the `<2` ceiling comment.
7. Re-check whether `mcp.server.auth.provider` import paths and `MCPServer`'s constructor signature
   have moved again between now and whenever this is picked up — re-verify against the actual
   installed package rather than trusting this document, since it was written against 2.0.0 exactly
   and the package may have moved further by the time this is unblocked.

## Verification

```bash
cd agents/python
uv run pytest packages/mcp-product/tests packages/mcp-inventory/tests tests/test_mcp_oauth_integration.py -v
docker compose --profile mcp --profile agents up --build
# then drive a real product-discovery query through the chat endpoint with MCP_ENABLED=true
```
