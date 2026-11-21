# MCP Integration

E-Commerce Agents ships two standalone MCP servers as independently publishable Python packages.
They expose product and inventory data over the [Model Context Protocol](https://modelcontextprotocol.io)
streamable HTTP transport so any MCP-compatible client can consume them without knowing anything
about this codebase. This page is for anyone connecting a client to them or switching the agents to MCP mode.

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
  → MCP Server (MCPServer + asyncpg)
  → PostgreSQL
```

The agent's behavior — prompts, routing, middleware, guardrails — is identical in both modes.
Only the data access layer changes.

## MCP Servers

| Server | Port | Domain | Package |
|--------|------|--------|---------|
| `mcp-product` | 9000 | Product search, details, comparison, trending, price history | `packages/mcp-product` |
| `mcp-inventory` | 9001 | Stock levels, warehouses, shipping, carriers | `packages/mcp-inventory` |

Both use `MCPServer` from the [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) 2.x (the class named `FastMCP`, in `mcp.server.fastmcp`, in SDK 1.x) and expose the MCP streamable
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
- `MCPServer(token_verifier=..., auth=AuthSettings(issuer_url=..., resource_server_url=..., required_scopes=[...], validate_token_resource=False))`
  — the MCP Python SDK auto-mounts `GET /.well-known/oauth-protected-resource/mcp` and wraps
  `POST /mcp` in `RequireAuthMiddleware`. An unauthenticated or wrong-scope call gets `401` plus a
  spec-shaped header: `WWW-Authenticate: Bearer error="invalid_token", error_description="...", resource_metadata="http://.../.well-known/oauth-protected-resource/mcp"`.
- `validate_token_resource=False` turns off the SDK's RFC 8707 resource-indicator check, because the vendored
  verifier checks the audience itself. The check defaults to on in SDK 3.0, and 2.x warns when the setting is
  unset. Note too that 2.x preserves a path-less issuer exactly, so the protected-resource
  metadata lists `authorization_servers` without a trailing slash.
- Each app is built with `mcp.streamable_http_app(host="0.0.0.0")` explicitly (in 2.x `host` moved from
  the constructor to this call). **Gotcha**: MCPServer auto-enables
  DNS-rebinding Host-header protection whenever `host` is left at its default `"127.0.0.1"`,
  allowlisting only `localhost`/`127.0.0.1`/`::1` — which would silently `421` every real call over
  the Docker network (e.g. `http://mcp-product:9000/mcp`), auth or no auth. Both `server.py` files pass it;
  do not remove the explicit `host` argument.
- The OAuth-mode client (`shared/oauth/service_client.py::build_mcp_http_client`) returns an
  `httpx2.AsyncClient`: the 2.x client stack, and so MAF's `MCPStreamableHTTPTool(http_client=...)`,
  does not accept an `httpx.AsyncClient`. Core 1.25+ also imports `tenacity` for MCP catalog recovery
  but only lists it in its `all` extra, so `tenacity` is a direct dependency here.
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
(real JSON-RPC over streamable HTTP at `POST /mcp`, same transport shape as the Python MCPServer
servers) with its own bearer-token gate (`GET /.well-known/oauth-protected-resource`, the same
`WWW-Authenticate` shape on 401) implemented as ASP.NET Core middleware ahead of the SDK's own
routing, reusing the `JwtTokenService`/`JwksKeyProvider` that validate user and inter-agent tokens.

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

Both MCP servers have no auth surface at all. A test guards that byte for byte:
`tests/test_mcp_oauth_integration.py::test_mcp_auth_disabled_is_unchanged_regression_guard`.

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
from agent_framework import MCPStreamableHTTPTool
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
    pyproject.toml          # name = "ecommerce-mcp-product"; PyPI metadata
    README.md  LICENSE      # the PyPI page; MIT
    src/ecommerce_mcp_product/
      server.py             # MCPServer + ASGI app
    tests/
  mcp-inventory/
    pyproject.toml          # name = "ecommerce-mcp-inventory"; PyPI metadata
    README.md  LICENSE
    src/ecommerce_mcp_inventory/
      server.py
    tests/
```

Both are members of the `agents/python` uv workspace. A single `uv.lock` covers the whole
workspace; the MCP packages share resolved deps without re-pinning.

## Publishing a server independently

Both packages are built to be published to PyPI as `ecommerce-mcp-product` and `ecommerce-mcp-inventory`.
Each carries its own metadata, `LICENSE` and a README that states the database schema it expects, how to
point it at a database, its transports and the optional OAuth settings — that README is the PyPI page.

Once published, any MCP client can run a server without the rest of this repo:

```bash
DATABASE_URL=postgresql://... uvx ecommerce-mcp-product                       # HTTP on :9000, endpoint /mcp
DATABASE_URL=postgresql://... uvx ecommerce-mcp-product --transport stdio     # for clients that launch it
```

Maintainers: the checks CI runs and the release steps are under
[Releasing the packages](#releasing-the-packages-maintainers) at the end of this page.

## Using from external MCP clients

Because these are standard MCP servers, any MCP-compatible client can connect. Clients that launch the
server themselves use the stdio transport (`--transport stdio`); the console script's default is streamable
HTTP, which is what `MCPStreamableHTTPTool` and the LangChain adapter below connect to:

```json
// Claude Desktop — claude_desktop_config.json
{
  "mcpServers": {
    "ecommerce-product": {
      "command": "uvx",
      "args": ["ecommerce-mcp-product", "--transport", "stdio"],
      "env": { "DATABASE_URL": "postgresql://..." }
    },
    "ecommerce-inventory": {
      "command": "uvx",
      "args": ["ecommerce-mcp-inventory", "--transport", "stdio"],
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

## Adding a new MCP server

1. Create a new workspace package:

```bash
mkdir -p agents/python/packages/mcp-<domain>/src/ecommerce_mcp_<domain>
```

2. Add `pyproject.toml` mirroring the existing packages (name `ecommerce-mcp-<domain>`,
   deps `mcp[cli]`, `asyncpg`, `uvicorn`, console script entry-point).

3. Write `server.py` using MCPServer:

```python
from mcp.server.mcpserver import MCPServer
from typing import Annotated

mcp = MCPServer("my-domain-mcp", lifespan=_lifespan)

@mcp.tool()
async def my_tool(param: Annotated[str, "Description"]) -> dict:
    ...

app = mcp.streamable_http_app(host="0.0.0.0")  # ASGI entry-point for uvicorn
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

## Releasing the packages (maintainers)

For whoever cuts a release of the two MCP packages. Nothing here is needed to run or connect to a server.

### What CI checks on every change

The `MCP packages · smoke tests` job in `tests.yml` builds the sdist and wheel of each package
(`uv build`), runs `twine check --strict` on them, installs the wheel into an empty virtualenv and starts the
console script with `--help`. The integration job starts the real console script against the test Postgres and
speaks MCP to it — `initialize`, `tools/list` and a tool call — once over streamable HTTP and once over stdio
(`tests/test_entrypoint.py` in each package). Nothing is uploaded.

### Release procedure

Releases are cut by pushing a tag. `.github/workflows/publish-mcp.yml` runs **only** on tags matching
`mcp-product-v*` / `mcp-inventory-v*` and on a manual dispatch; an ordinary push or pull request never
triggers it, and it holds no stored token.

1. **Bump the version** in the package's `pyproject.toml` (`agents/python/packages/mcp-<domain>/`) and merge
   it to `main` through a normal pull request. Dependencies are resolved from the workspace `uv.lock`; if you
   change a dependency range, run `uv lock` in `agents/python` and commit the result.
2. **Dry run on TestPyPI (recommended for the first release of each package):** Actions → *Publish MCP
   packages* → *Run workflow*, choose the package. It builds and checks, then uploads to TestPyPI through the
   `testpypi` environment. TestPyPI refuses to overwrite a version, so a retry needs a new version number.
   Check the page, then `uvx --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/
   ecommerce-mcp-product --help`.
3. **Tag the merge commit** and push the tag — the tag is `mcp-<domain>-v<version>`, and the version must be the
   one in `pyproject.toml`:

   ```bash
   git tag mcp-product-v0.1.0 && git push origin mcp-product-v0.1.0
   ```

4. The workflow then, for that one package only: refuses to continue unless the tag's version equals the
   package's `pyproject.toml` version and the tagged commit is on `main`; runs the package's non-integration
   tests; builds sdist + wheel; runs `twine check`; installs the wheel into an empty environment; and uploads
   to PyPI via trusted publishing (`pypa/gh-action-pypi-publish`, `id-token: write`, environment `pypi`).
   A mismatch fails the run before anything is uploaded.
5. Each package is versioned and tagged independently of the other, and of the application's `vX.Y.Z` tags
   (`release.yml` only matches `v*.*.*`).

The version and ancestry checks in step 4 are a convenience that catches mistakes. They are **not** a
security boundary: the tag ruleset and `pypi` environment rules in "What only the maintainer can do", step 4,
are what make a release trustworthy.

A PyPI upload cannot be replaced: to fix a bad release, yank it on PyPI and publish the next version.

### What only the maintainer can do

The workflow cannot do these, and it fails at the upload step until they are done. As far as this repo records,
nothing is published or registered yet.

1. **Confirm the package names are free.** `ecommerce-mcp-product` and `ecommerce-mcp-inventory` have not been
   checked against PyPI. Look at
   `https://pypi.org/project/<name>/` (a 404 means unclaimed; PyPI also rejects names too similar to an existing
   one at upload time). If a name is taken, rename the package in its `pyproject.toml`, in `publish-mcp.yml`'s
   `dist_name` check and in its README.
2. **Register a trusted publisher for each project** on [pypi.org](https://pypi.org/manage/account/publishing/).
   Neither project exists yet, so add it as a *pending publisher*, with:

   | Field | Value |
   |-------|-------|
   | PyPI project name | `ecommerce-mcp-product` (then `ecommerce-mcp-inventory`) |
   | Owner | `nitin27may` |
   | Repository name | `e-commerce-agents` |
   | Workflow name | `publish-mcp.yml` |
   | Environment name | `pypi` |

   For the TestPyPI dry run, register the same on [test.pypi.org](https://test.pypi.org/manage/account/publishing/)
   with environment name `testpypi`. The two indexes are separate accounts.
3. **Create the GitHub environments** `pypi` and `testpypi` (Settings → Environments). A missing environment is
   created empty on first use, which means no approval gate, so configure `pypi` as in the next section.
4. **REQUIRED: protect the release path (tag ruleset and the `pypi` environment).** Without this the
   in-workflow checks are advisory only. A tag push runs the workflow file *as it exists at the tagged commit*,
   so whoever can push a tag can also push a commit that deletes the "version equals `pyproject.toml`" and
   "commit is on `main`" checks and tag that commit. No edit to `publish-mcp.yml` can close this; only
   repository settings can:
   - **Tag ruleset** (Settings → Rules → Rulesets → New tag ruleset): target `mcp-*-v*`, enforcement *Active*,
     enable *Restrict creations*, *Restrict updates* and *Restrict deletions*, and put only the release
     maintainers on the bypass list. Then only they can create a release tag at all.
   - **`pypi` environment** (Settings → Environments → `pypi`): under *Deployment branches and tags* choose
     *Selected branches and tags* and add the tag pattern `mcp-*-v*` (and no branch); add at least one
     *Required reviewer* who is not the person who pushed the tag, and leave *Prevent self-review* on. The
     trusted publisher on PyPI is bound to this environment name, so a run that does not satisfy these rules
     cannot obtain an upload token.
   Do the same for `testpypi` if you want the dry run gated (it runs on manual dispatch, so restrict it to
   the `main` branch instead of a tag pattern).

## Related

- [`docs/architecture.md`](architecture.md) — full system architecture
- [`docs/telemetry.md`](telemetry.md) — OTel + Langfuse observability
- [`docs/maf-best-practices.md`](maf-best-practices.md) — MAF patterns used across all agents
