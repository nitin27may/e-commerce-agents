# ecommerce-mcp-inventory

Standalone MCP server for the e-commerce inventory and fulfillment domain. Exposes stock levels,
warehouse availability, restock schedules, shipping estimates, and carrier comparison over the
[MCP](https://modelcontextprotocol.io) streamable HTTP transport.

Part of the [E-Commerce Agents](https://github.com/nitinksingh/e-commerce-agents) demo platform.

## Tools

| Tool | Description |
|------|-------------|
| `check_stock` | Live stock levels across all regional warehouses for a product |
| `get_warehouse_availability` | Warehouse inventory + upcoming restock schedule |
| `get_restock_schedule` | Upcoming restock dates and quantities |
| `estimate_shipping` | Shipping cost and delivery time from nearest stocked warehouse |
| `compare_carriers` | All carriers between two regions with pricing and delivery times |

## Requirements

- Python 3.12+
- PostgreSQL 16+ with the e-commerce schema loaded (see [docker/postgres/init.sql](../../docker/postgres/init.sql))

## Install

```bash
pip install ecommerce-mcp-inventory
# or
uv add ecommerce-mcp-inventory
```

## Run

```bash
# HTTP service (default port 9001)
ecommerce-mcp-inventory

# Custom port
PORT=9001 ecommerce-mcp-inventory

# Directly via uvicorn
uvicorn ecommerce_mcp_inventory.server:app --host 0.0.0.0 --port 9001

# MCP Inspector (stdio)
uv run python -m ecommerce_mcp_inventory.server
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql://ecommerce:ecommerce_secret@localhost:5432/ecommerce_agents` | PostgreSQL connection string |
| `PORT` | `9001` | Port for the HTTP server (console script only) |

## Inspect with MCP Inspector

```bash
npx @modelcontextprotocol/inspector http://localhost:9001/mcp
```

## Publish

```bash
uv build --package ecommerce-mcp-inventory
uv publish dist/ecommerce_mcp_inventory-*.whl
```

## Use from any MCP client

```python
# With MAF (Microsoft Agent Framework)
from agent_framework import MCPStreamableHTTPTool

mcp_inventory = MCPStreamableHTTPTool(
    name="inventory-mcp",
    url="http://localhost:9001/mcp",
    description="Inventory and fulfillment data",
)
```

```json
// Claude Desktop (claude_desktop_config.json)
{
  "mcpServers": {
    "ecommerce-inventory": {
      "command": "ecommerce-mcp-inventory",
      "env": {
        "DATABASE_URL": "postgresql://..."
      }
    }
  }
}
```
