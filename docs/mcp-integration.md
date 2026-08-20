# MCP Integration

E-Commerce Agents ships two standalone MCP servers as independently publishable Python packages.
They expose product and inventory data over the [Model Context Protocol](https://modelcontextprotocol.io)
streamable HTTP transport so any MCP-compatible client can consume them without knowing anything
about this codebase.

## What this demonstrates

The default architecture has specialist agents calling PostgreSQL directly via `asyncpg`:

```
Specialist Agent (MAF)
  → @tool function
  → asyncpg
  → PostgreSQL
```

With MCP enabled, the same agents call a running MCP server instead:

```
Specialist Agent (MAF)
  → MCPStreamableHTTPTool (MAF's built-in MCP client)
  → Streamable HTTP (MCP protocol)
  → MCP Server (FastMCP + asyncpg)
  → PostgreSQL
```

The agent's behavior — prompts, routing, middleware, guardrails — is identical in both modes.
Only the data access layer changes.

## MCP Servers

| Server | Port | Domain | Package |
|--------|------|--------|---------|
| `mcp-product` | 9000 | Product search, details, comparison, trending, price history | `packages/mcp-product` |
| `mcp-inventory` | 9001 | Stock levels, warehouses, shipping, carriers | `packages/mcp-inventory` |

Both use [FastMCP](https://github.com/modelcontextprotocol/python-sdk) and expose the MCP streamable
HTTP transport at `/mcp`. MAF's `MCPStreamableHTTPTool` connects to that endpoint.

## Enabling MCP mode

### 1. Start the MCP servers

```bash
# Start MCP servers alongside infrastructure
docker compose --profile mcp --profile agents up
```

Or locally for development:

```bash
cd agents/python

# Product MCP server on :9000
uv run uvicorn ecommerce_mcp_product.server:app --port 9000 --reload &

# Inventory MCP server on :9001
uv run uvicorn ecommerce_mcp_inventory.server:app --port 9001 --reload &
```

### 2. Set environment variables

```bash
MCP_ENABLED=true
MCP_PRODUCT_SERVER_URL=http://localhost:9000/mcp    # or http://mcp-product:9000/mcp in Docker
MCP_INVENTORY_SERVER_URL=http://localhost:9001/mcp  # or http://mcp-inventory:9001/mcp in Docker
```

### 3. Restart the specialist agents

The `product-discovery` and `inventory-fulfillment` agents read `MCP_ENABLED` at startup and
select the appropriate tool set. No code changes are needed.

## OAuth 2.1 resource-server mode (optional)

By default the MCP servers are unauthenticated — anyone who can reach `:9000`/`:9001` can call
tools. Setting `MCP_AUTH_ENABLED=true` (requires `AUTH_MODE=oauth` and `MCP_ENABLED=true`) turns
each server into an OAuth 2.1 resource server per [RFC 9728](https://datatracker.ietf.org/doc/html/rfc9728),
validated against the same self-hosted Authorization Server (AS) used for user login and
inter-agent calls — no external identity provider.

### Running it locally

```bash
# Full stack, oauth mode, MCP enabled + protected
AUTH_MODE=oauth MCP_ENABLED=true MCP_AUTH_ENABLED=true \
  docker compose --profile agents --profile mcp up --build

# Seed the database, then restart auth-server so its in-memory client
# registry picks up the seeded oauth_clients rows (it loads once at startup)
docker compose --profile seed run --rm seeder
docker compose restart auth-server
```

### What each server does differently

- A vendored `JwksTokenVerifier` (`packages/mcp-product/src/ecommerce_mcp_product/auth.py`,
  `packages/mcp-inventory/.../auth.py` — deliberately **not** shared between the two packages or
  with the main app's `shared/oauth/verifier.py`, since these are independently publishable uv
  workspace members) validates the bearer JWT via `PyJWKClient` against `AUTH_SERVER_JWKS_URL`:
  signature, issuer (`AUTH_SERVER_ISSUER`), audience (`mcp-product` / `mcp-inventory`), and required
  scope (`mcp:product` / `mcp:inventory`).
- `FastMCP(token_verifier=..., auth=AuthSettings(issuer_url=..., resource_server_url=..., required_scopes=[...]))`
  — the MCP Python SDK auto-mounts `GET /.well-known/oauth-protected-resource/mcp` and wraps
  `POST /mcp` in `RequireAuthMiddleware`. An unauthenticated or wrong-scope call gets `401` plus a
  spec-shaped header: `WWW-Authenticate: Bearer error="invalid_token", error_description="...", resource_metadata="http://.../.well-known/oauth-protected-resource/mcp"`.
- Each server is served with `host="0.0.0.0"` explicitly. **Gotcha**: FastMCP auto-enables
  DNS-rebinding Host-header protection whenever `host` is left at its default `"127.0.0.1"`,
  allowlisting only `localhost`/`127.0.0.1`/`::1` — which would silently `421` every real call over
  the Docker network (e.g. `http://mcp-product:9000/mcp`), auth or no auth. This is fixed in both
  `server.py` files; don't remove the explicit `host` argument.
- The Dockerfile/compose healthchecks curl `/mcp` when `MCP_AUTH_ENABLED=false`, but the
  auto-mounted, unauthenticated `/.well-known/oauth-protected-resource/mcp` when it's `true` — `/mcp`
  always `401`s once auth is on, so the healthcheck target has to switch too.

### How a specialist agent acquires its resource token

`product_discovery/agent.py` / `inventory_fulfillment/agent.py` pass a `header_provider` to
`MCPStreamableHTTPTool` (MAF's own documented mechanism for attaching per-request headers) when
`MCP_AUTH_ENABLED=true`:

```python
mcp_product = MCPStreamableHTTPTool(
    name="product-mcp",
    url=settings.MCP_PRODUCT_SERVER_URL,
    header_provider=mcp_header_provider(settings.MCP_PRODUCT_REQUIRED_SCOPE, settings.MCP_PRODUCT_AUDIENCE),
)
```

`header_provider` is invoked **synchronously**, from inside an already-running event loop — it
cannot itself perform the `client_credentials` grant. Each specialist's async startup hook
pre-warms the token cache once (`await acquire_service_token(scope, audience)`); the header
provider then does a synchronous, cache-only read (`get_cached_service_token`) and attaches
`Authorization: Bearer <token>`. A token scoped for `mcp:product` cannot authenticate to
`mcp-inventory`, or vice versa — each server validates its own audience independently.

### Connecting an external MCP client (e.g. MCP Inspector) in oauth mode

A generic OAuth 2.1 client completes the standard protected-resource discovery flow:

1. `GET http://localhost:9000/.well-known/oauth-protected-resource/mcp` → `{"resource": "...", "authorization_servers": ["http://localhost:8090/"], "scopes_supported": ["mcp:product"], ...}`
2. Discover the AS's own metadata: `GET http://localhost:8090/.well-known/oauth-authorization-server`
3. Obtain a token from `token_endpoint` (`client_credentials` grant, scope `mcp:product`) using a
   seeded client's credentials (`scripts/seed.py::OAUTH_CLIENTS` — e.g. `product-discovery`; derive
   the dev secret with `derive_client_secret(OAUTH_SEED_KEY, client_id)`, or set an explicit
   `OAUTH_CLIENT_SECRET` in production)
4. Call `POST /mcp` with `Authorization: Bearer <token>`

The .NET MCP host (`ECommerceAgents.Mcp`) uses the official `ModelContextProtocol.AspNetCore` SDK
(real JSON-RPC over streamable HTTP at `POST /mcp`, same transport shape as the Python FastMCP
servers) with its own bearer-token gate (`GET /.well-known/oauth-protected-resource`, the same
`WWW-Authenticate` shape on 401) implemented as ASP.NET Core middleware ahead of the SDK's own
routing, reusing the same `JwtTokenService`/`JwksKeyProvider` the Phase B/C auth paths use.

### Getting credentials as a third-party MCP client (dynamic registration)

Step 3 above assumes a first-party, pre-seeded client. A genuinely external MCP client can instead
self-register (RFC 7591) when the AS operator has opted in:

1. Operator sets `AUTH_ALLOW_DYNAMIC_REGISTRATION=true` on the auth-server (off by default).
2. Operator obtains a `client:register`-scoped token via `client_credentials` using the seeded
   `auth-admin` client, and hands the resulting **registration token** to the third party
   out-of-band (it is not something a client discovers on its own).
3. The client calls `POST /oauth/register` with that bearer token and a body like
   `{"client_name": "...", "scope": "mcp:product"}`, and gets back a `client_id`/`client_secret`
   pair (shown once) — usable immediately with step 3 above.

Registration is deliberately narrow: only `client_credentials` grant, and only the two MCP read
scopes (`mcp:product`, `mcp:inventory`) can be requested — never `agent:invoke`, `api:chat`, or
`client:register` itself. See `docs/security-guide.md`'s Known Issues for the one non-obvious
implementation detail (the registration endpoint verifies its bearer token entirely in-process,
not via the JWKS-over-HTTP path every other resource server uses — that path deadlocks when a
single-worker server tries to fetch its own JWKS from within its own request handler).

### Flag off (`MCP_AUTH_ENABLED=false`, the default)

Both MCP servers behave exactly as before this feature — no auth surface at all, byte-for-byte
regression-guarded by `tests/test_mcp_oauth_integration.py::test_mcp_auth_disabled_is_unchanged_regression_guard`.

## Inspect with MCP Inspector

The [MCP Inspector](https://modelcontextprotocol.io/docs/tools/inspector) is an interactive tool
for testing MCP servers. With the servers running:

```bash
# Inspect the product server
npx @modelcontextprotocol/inspector http://localhost:9000/mcp

# Inspect the inventory server
npx @modelcontextprotocol/inspector http://localhost:9001/mcp
```

This lets you browse tool schemas, call individual tools, and see raw MCP protocol messages.

## How the agent selection works

In `product_discovery/agent.py` and `inventory_fulfillment/agent.py`:

```python
from agent_framework._mcp import MCPStreamableHTTPTool
from shared.config import settings

def create_product_discovery_agent() -> Agent:
    if settings.MCP_ENABLED:
        mcp_product = MCPStreamableHTTPTool(
            name="product-mcp",
            url=settings.MCP_PRODUCT_SERVER_URL,
            description="Product catalog data via MCP",
        )
        # User-context tools (semantic search, price history) stay local —
        # they depend on pgvector / ContextVars not propagated to the MCP server.
        tools = [mcp_product, semantic_search, find_similar_products, ...]
    else:
        tools = AGENT_TOOLS  # direct asyncpg @tool functions

    return Agent(client=..., tools=tools, ...)
```

`MCPStreamableHTTPTool` is MAF's built-in MCP client. When the agent initialises, it calls the
MCP server's tool listing endpoint, discovers the available tools, and exposes them to the LLM
exactly like native `@tool` functions. The LLM cannot tell the difference.

## Tool coverage

Not all tools are migrated to MCP. Tools that require user identity context (ContextVars set by
the auth middleware) or are unique to this platform (semantic vector search, `place_backorder`)
remain as direct `@tool` functions even in MCP mode. The MCP servers cover pure data-access tools
that are genuinely portable.

| Tool | MCP mode | Direct mode |
|------|----------|-------------|
| `search_products` | product-mcp server | asyncpg `@tool` |
| `get_product_details` | product-mcp server | asyncpg `@tool` |
| `compare_products` | product-mcp server | asyncpg `@tool` |
| `get_trending_products` | product-mcp server | asyncpg `@tool` |
| `get_price_history` | product-mcp server | asyncpg `@tool` |
| `semantic_search` | direct `@tool` (pgvector) | asyncpg `@tool` |
| `find_similar_products` | direct `@tool` (pgvector) | asyncpg `@tool` |
| `check_stock` | inventory-mcp server | asyncpg `@tool` |
| `get_warehouse_availability` | inventory-mcp server | asyncpg `@tool` |
| `estimate_shipping` | inventory-mcp server | asyncpg `@tool` |
| `compare_carriers` | inventory-mcp server | asyncpg `@tool` |
| `get_restock_schedule` | inventory-mcp server | asyncpg `@tool` |
| `get_tracking_status` | direct `@tool` | asyncpg `@tool` |
| `place_backorder` | direct `@tool` | asyncpg `@tool` |

## Package structure

Each MCP server is a standalone Python package under `agents/python/packages/`:

```
agents/python/packages/
  mcp-product/
    pyproject.toml          # name = "ecommerce-mcp-product"
    src/ecommerce_mcp_product/
      server.py             # FastMCP server + ASGI app
    tests/
  mcp-inventory/
    pyproject.toml          # name = "ecommerce-mcp-inventory"
    src/ecommerce_mcp_inventory/
      server.py
    tests/
```

Both are members of the `agents/python` uv workspace. A single `uv.lock` covers the whole
workspace; the MCP packages share resolved deps without re-pinning.

## Publishing a server independently

```bash
cd agents/python

# Build wheel + sdist
uv build --package ecommerce-mcp-product
uv build --package ecommerce-mcp-inventory

# Publish to PyPI (or a private registry)
uv publish dist/ecommerce_mcp_product-*.whl
uv publish dist/ecommerce_mcp_inventory-*.whl
```

Once published, any MCP client can install and run the server without the rest of this repo:

```bash
pip install ecommerce-mcp-product
DATABASE_URL=postgresql://... ecommerce-mcp-product   # starts on :9000
```

## Adding a new MCP server

1. Create a new workspace package:

```bash
mkdir -p agents/python/packages/mcp-<domain>/src/ecommerce_mcp_<domain>
```

2. Add `pyproject.toml` mirroring the existing packages (name `ecommerce-mcp-<domain>`,
   deps `mcp[cli]`, `asyncpg`, `uvicorn`, console script entry-point).

3. Write `server.py` using FastMCP:

```python
from mcp.server.fastmcp import FastMCP
from typing import Annotated

mcp = FastMCP("my-domain-mcp", lifespan=_lifespan)

@mcp.tool()
async def my_tool(param: Annotated[str, "Description"]) -> dict:
    ...

app = mcp.streamable_http_app()  # ASGI entry-point for uvicorn
```

4. Register the package in the workspace root `pyproject.toml`:

```toml
[tool.uv.workspace]
members = ["packages/mcp-product", "packages/mcp-inventory", "packages/mcp-<domain>"]
```

5. Run `uv lock` to update the shared lockfile.

6. Add a service to `docker-compose.yml` under the `mcp` profile using `Dockerfile.mcp`.

7. Add config vars to `shared/config.py` and `.env.example`.

8. Wire `MCPStreamableHTTPTool` into the relevant agent factory.

## Using from external MCP clients

Because these are standard MCP servers, any MCP-compatible client can connect:

```json
// Claude Desktop — claude_desktop_config.json
{
  "mcpServers": {
    "ecommerce-product": {
      "command": "ecommerce-mcp-product",
      "env": { "DATABASE_URL": "postgresql://..." }
    },
    "ecommerce-inventory": {
      "command": "ecommerce-mcp-inventory",
      "env": { "DATABASE_URL": "postgresql://..." }
    }
  }
}
```

```python
# LangGraph / LangChain
from langchain_mcp_adapters.client import MultiServerMCPClient

client = MultiServerMCPClient({
    "product": {"url": "http://localhost:9000/mcp", "transport": "streamable_http"},
    "inventory": {"url": "http://localhost:9001/mcp", "transport": "streamable_http"},
})
```

## Related

- [`docs/architecture.md`](architecture.md) — full system architecture
- [`docs/telemetry.md`](telemetry.md) — OTel + Langfuse observability
- [`docs/maf-best-practices.md`](maf-best-practices.md) — MAF patterns used across all agents
