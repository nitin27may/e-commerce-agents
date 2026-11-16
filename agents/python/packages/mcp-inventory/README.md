# ecommerce-mcp-inventory

A [Model Context Protocol](https://modelcontextprotocol.io) server for e-commerce **inventory and
fulfillment** data held in PostgreSQL. It exposes stock levels, warehouse availability, restock schedules,
shipping estimates and carrier comparison as MCP tools, over streamable HTTP or stdio.

Part of the [E-Commerce Agents](https://github.com/nitin27may/e-commerce-agents) demo platform, and usable on
its own: point it at a database with the schema below and any MCP client can query it. The server is
**read-only** — it only runs `SELECT`s.

## Tools

| Tool | Description |
|------|-------------|
| `check_stock` | Live stock levels across all regional warehouses for a product |
| `get_warehouse_availability` | Warehouse inventory + upcoming restock schedule |
| `get_restock_schedule` | Upcoming restock dates and quantities |
| `estimate_shipping` | Shipping cost and delivery time from the nearest stocked warehouse |
| `compare_carriers` | All carriers between two regions with pricing and delivery times |

## Install and run

Python 3.12+.

```bash
uvx ecommerce-mcp-inventory                                # run without installing
# or
pip install ecommerce-mcp-inventory && ecommerce-mcp-inventory
```

```bash
DATABASE_URL=postgresql://user:password@db-host:5432/shop ecommerce-mcp-inventory
# MCP endpoint: http://localhost:9001/mcp
```

## Pointing it at a database

The server connects with `DATABASE_URL` (a libpq-style URL, read at start-up) and opens a small connection pool
(2–8 connections). If the variable is unset it falls back to the demo platform's local database
(`postgresql://ecommerce:ecommerce_secret@localhost:5432/ecommerce_agents`) — set it explicitly for anything
else. Use a **read-only** database role: the server never writes.

## Database schema the server expects

The server does not create or migrate anything. It expects these tables and columns to exist, with these names;
it reads nothing else.

| Table | Columns used |
|-------|--------------|
| `warehouse_inventory` | `warehouse_id`, `product_id` (uuid), `quantity`, `reorder_threshold` |
| `warehouses` | `id`, `name`, `location`, `region` (`east` / `central` / `west` in the demo data) |
| `restock_schedule` | `product_id`, `warehouse_id`, `expected_quantity`, `expected_date` (date) |
| `shipping_rates` | `carrier_id`, `region_from`, `region_to`, `price`, `estimated_days_min`, `estimated_days_max` |
| `carriers` | `id`, `name`, `speed_tier` |

`product_id` values are uuids supplied by the caller; the server does not read the `products` table itself, so
the ids only need to match what your `warehouse_inventory` and `restock_schedule` rows hold. Regions are plain
strings: `estimate_shipping` prefers a warehouse in the destination region, then `central`, then any other.

The complete reference DDL (and sample data) is in the repository:
[`docker/postgres/init.sql`](https://github.com/nitin27may/e-commerce-agents/blob/main/docker/postgres/init.sql)
plus the numbered files in
[`docker/postgres/migrations/`](https://github.com/nitin27may/e-commerce-agents/tree/main/docker/postgres/migrations).
If your system uses different table or column names, this server will not work against it without changes.

## Transport

| Mode | How | Notes |
|------|-----|-------|
| Streamable HTTP (default) | `ecommerce-mcp-inventory` | Listens on `0.0.0.0:$PORT` (default `9001`); MCP endpoint at `/mcp` |
| stdio | `ecommerce-mcp-inventory --transport stdio` (or `MCP_TRANSPORT=stdio`) | For clients that launch the server themselves |

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
| `MCP_INVENTORY_AUDIENCE` | `mcp-inventory` | Required `aud` claim |
| `MCP_INVENTORY_REQUIRED_SCOPE` | `mcp:inventory` | Scope the token must carry (`scope` claim, space-separated) |
| `MCP_INVENTORY_RESOURCE_URL` | `http://localhost:9001/mcp` | This server's public URL, advertised in the metadata |

The defaults point at the demo platform's own authorization server; set them all for your own.

## Other settings

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | demo database (see above) | PostgreSQL connection URL |
| `PORT` | `9001` | HTTP port (HTTP transport only) |
| `MCP_TRANSPORT` | `http` | `http` or `stdio`; the `--transport` flag wins |

## Example client configuration

Claude Desktop (`claude_desktop_config.json`) — the client launches the server over stdio:

```json
{
  "mcpServers": {
    "ecommerce-inventory": {
      "command": "uvx",
      "args": ["ecommerce-mcp-inventory", "--transport", "stdio"],
      "env": { "DATABASE_URL": "postgresql://user:password@db-host:5432/shop" }
    }
  }
}
```

A client connecting to a running HTTP server — for example with the Microsoft Agent Framework:

```python
from agent_framework import MCPStreamableHTTPTool

mcp_inventory = MCPStreamableHTTPTool(
    name="inventory-mcp",
    url="http://localhost:9001/mcp",
    description="Inventory and fulfillment data",
)
```

Try it with the MCP Inspector: `npx @modelcontextprotocol/inspector http://localhost:9001/mcp`.

## License

MIT. Source, issues and the surrounding platform:
[github.com/nitin27may/e-commerce-agents](https://github.com/nitin27may/e-commerce-agents).
