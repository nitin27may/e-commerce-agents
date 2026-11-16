# ecommerce-mcp-product

Standalone MCP server for the e-commerce product catalog. Exposes product search, details,
comparison, trending products, and price history over the [MCP](https://modelcontextprotocol.io)
streamable HTTP transport.

Part of the [E-Commerce Agents](https://github.com/nitinksingh/e-commerce-agents) demo platform.

## Tools

| Tool | Description |
|------|-------------|
| `search_products` | Keyword + filter search (category, price range, rating, sort) |
| `get_product_details` | Full product details including specs, stock status, and seller |
| `compare_products` | Side-by-side comparison of 2–3 products |
| `get_trending_products` | Top products by recent order volume, optionally filtered by category |
| `get_price_history` | Price trend with avg/min/max and deal-quality signal |

## Requirements

- Python 3.12+
- PostgreSQL 16+ with the e-commerce schema loaded (see [docker/postgres/init.sql](../../docker/postgres/init.sql))

## Install

```bash
pip install ecommerce-mcp-product
# or
uv add ecommerce-mcp-product
```

## Run

```bash
# HTTP service (default port 9000)
ecommerce-mcp-product

# Custom port
PORT=9000 ecommerce-mcp-product

# Directly via uvicorn
uvicorn ecommerce_mcp_product.server:app --host 0.0.0.0 --port 9000

# MCP Inspector (stdio)
uv run python -m ecommerce_mcp_product.server
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql://ecommerce:ecommerce_secret@localhost:5432/ecommerce_agents` | PostgreSQL connection string |
| `PORT` | `9000` | Port for the HTTP server (console script only) |

## Inspect with MCP Inspector

```bash
npx @modelcontextprotocol/inspector http://localhost:9000/mcp
```

## Publish

```bash
uv build --package ecommerce-mcp-product
uv publish dist/ecommerce_mcp_product-*.whl
```

## Use from any MCP client

```python
# With MAF (Microsoft Agent Framework)
from agent_framework import MCPStreamableHTTPTool

mcp_product = MCPStreamableHTTPTool(
    name="product-mcp",
    url="http://localhost:9000/mcp",
    description="Product catalog data",
)
```

```json
// Claude Desktop (claude_desktop_config.json)
{
  "mcpServers": {
    "ecommerce-product": {
      "command": "ecommerce-mcp-product",
      "env": {
        "DATABASE_URL": "postgresql://..."
      }
    }
  }
}
```
