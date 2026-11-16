# ecommerce-mcp-product

A [Model Context Protocol](https://modelcontextprotocol.io) server for an e-commerce **product catalog** held
in PostgreSQL. It exposes product search, details, comparison, trending products and price history as MCP
tools, over streamable HTTP or stdio.

Part of the [E-Commerce Agents](https://github.com/nitin27may/e-commerce-agents) demo platform, and usable on
its own: point it at a database with the schema below and any MCP client can query it. The server is
**read-only** — it only runs `SELECT`s.

## Tools

| Tool | Description |
|------|-------------|
| `search_products` | Full-text search plus filters (category, price range, rating, sort) |
| `get_product_details` | Full product details including specs, stock status, and seller |
| `compare_products` | Side-by-side comparison of 2–3 products |
| `get_trending_products` | Top products by recent order volume, optionally filtered by category |
| `get_price_history` | Price trend with avg/min/max and deal-quality signal |

## Install and run

Python 3.12+.

```bash
uvx ecommerce-mcp-product                                  # run without installing
# or
pip install ecommerce-mcp-product && ecommerce-mcp-product
```

```bash
DATABASE_URL=postgresql://user:password@db-host:5432/shop ecommerce-mcp-product
# MCP endpoint: http://localhost:9000/mcp
```

## Pointing it at a database

The server connects with `DATABASE_URL` (a libpq-style URL, read at start-up) and opens a small connection pool
(2–8 connections). If the variable is unset it falls back to the demo platform's local database
(`postgresql://ecommerce:ecommerce_secret@localhost:5432/ecommerce_agents`) — set it explicitly for anything
else. Use a **read-only** database role: the server never writes.

## Database schema the server expects

The server does not create or migrate anything. It expects these tables and columns to exist, with these names;
it reads nothing else. (PostgreSQL 12+ for the generated column; the full demo schema also needs the `vector`
extension for tables this server does not touch.)

| Table | Columns used |
|-------|--------------|
| `products` | `id` (uuid), `name`, `description`, `category`, `brand`, `price`, `original_price`, `rating`, `review_count`, `specs` (jsonb), `is_active`, `created_at`, `seller_id`, `search_vector` (tsvector) |
| `users` | `id`, `name` — joined on `products.seller_id` for the seller's name |
| `warehouse_inventory` | `product_id`, `quantity` — summed for stock status |
| `order_items` | `id`, `product_id`, `order_id` — for trending products |
| `orders` | `id`, `created_at` — for trending products |
| `price_history` | `product_id`, `price`, `recorded_at` |

`products.search_vector` is what `search_products` matches against with `@@` and ranks with `ts_rank`; the demo
schema defines it as a stored generated column, weighted name > brand > description:

```sql
search_vector tsvector GENERATED ALWAYS AS (
    setweight(to_tsvector('english', coalesce(name, '')), 'A') ||
    setweight(to_tsvector('english', coalesce(brand, '')), 'B') ||
    setweight(to_tsvector('english', coalesce(description, '')), 'C')
) STORED
-- and an index to keep it fast:  CREATE INDEX ON products USING GIN (search_vector);
```

The complete reference DDL (and sample data) is in the repository:
[`docker/postgres/init.sql`](https://github.com/nitin27may/e-commerce-agents/blob/main/docker/postgres/init.sql)
plus the numbered files in
[`docker/postgres/migrations/`](https://github.com/nitin27may/e-commerce-agents/tree/main/docker/postgres/migrations).
If your catalog uses different table or column names, this server will not work against it without changes.

## Transport

| Mode | How | Notes |
|------|-----|-------|
| Streamable HTTP (default) | `ecommerce-mcp-product` | Listens on `0.0.0.0:$PORT` (default `9000`); MCP endpoint at `/mcp` |
| stdio | `ecommerce-mcp-product --transport stdio` (or `MCP_TRANSPORT=stdio`) | For clients that launch the server themselves |

The server binds all interfaces. Without OAuth (below) **anyone who can reach the port can call the tools**, so
keep it on a private network or enable OAuth.

## OAuth 2.1 resource-server mode (optional, HTTP only)

Off by default. With `MCP_AUTH_ENABLED=true` the server requires a bearer token on `/mcp`, validates it against
an authorization server's JWKS, and publishes
[RFC 9728](https://datatracker.ietf.org/doc/html/rfc9728) protected-resource metadata at
`/.well-known/oauth-protected-resource/mcp`. Tokens must be RS256-signed JWTs. A missing, expired, wrong-issuer,
wrong-audience or wrong-scope token gets `401`.

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_AUTH_ENABLED` | `false` | Turn the resource-server mode on |
| `AUTH_SERVER_JWKS_URL` | `http://localhost:8090/.well-known/jwks.json` | Where signing keys are fetched |
| `AUTH_SERVER_ISSUER` | `http://localhost:8090` | Required `iss` claim; advertised in the metadata |
| `MCP_PRODUCT_AUDIENCE` | `mcp-product` | Required `aud` claim |
| `MCP_PRODUCT_REQUIRED_SCOPE` | `mcp:product` | Scope the token must carry (`scope` claim, space-separated) |
| `MCP_PRODUCT_RESOURCE_URL` | `http://localhost:9000/mcp` | This server's public URL, advertised in the metadata |

The defaults point at the demo platform's own authorization server; set them all for your own.

## Other settings

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | demo database (see above) | PostgreSQL connection URL |
| `PORT` | `9000` | HTTP port (HTTP transport only) |
| `MCP_TRANSPORT` | `http` | `http` or `stdio`; the `--transport` flag wins |

## Example client configuration

Claude Desktop (`claude_desktop_config.json`) — the client launches the server over stdio:

```json
{
  "mcpServers": {
    "ecommerce-product": {
      "command": "uvx",
      "args": ["ecommerce-mcp-product", "--transport", "stdio"],
      "env": { "DATABASE_URL": "postgresql://user:password@db-host:5432/shop" }
    }
  }
}
```

A client connecting to a running HTTP server — for example with the Microsoft Agent Framework:

```python
from agent_framework import MCPStreamableHTTPTool

mcp_product = MCPStreamableHTTPTool(
    name="product-mcp",
    url="http://localhost:9000/mcp",
    description="Product catalog data",
)
```

Try it with the MCP Inspector: `npx @modelcontextprotocol/inspector http://localhost:9000/mcp`.

## License

MIT. Source, issues and the surrounding platform:
[github.com/nitin27may/e-commerce-agents](https://github.com/nitin27may/e-commerce-agents).
