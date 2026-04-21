# .NET Port 06 — ReviewSentiment + InventoryFulfillment

## Goal

Port the two smallest specialists together: `agents/review_sentiment/*` and `agents/inventory_fulfillment/*`.

## Python source → .NET target

**ReviewSentiment (`:8084`)**:

| Python | .NET |
|--------|------|
| `review_sentiment/agent.py` | `ReviewSentimentAgent.cs` |
| `review_sentiment/tools.py` (summarize_reviews, sentiment_score, flag_review) | `ReviewSentimentTools.cs` |
| `review_sentiment/main.py` | `Program.cs` on `:8084` |

**InventoryFulfillment (`:8085`)**:

| Python | .NET |
|--------|------|
| `inventory_fulfillment/agent.py` | `InventoryFulfillmentAgent.cs` |
| `inventory_fulfillment/tools.py` (check_stock, estimate_shipping, list_warehouses) | `InventoryFulfillmentTools.cs` |
| `inventory_fulfillment/main.py` | `Program.cs` on `:8085` |

## Test projects

- `ECommerceAgents.ReviewSentiment.Tests/` — minimum **8 tests**: summarization, sentiment scoring, moderation flags, edge cases, A2A endpoint smoke.
- `ECommerceAgents.InventoryFulfillment.Tests/` — minimum **8 tests**: stock lookup, shipping estimation, warehouse listing, out-of-stock path, same A2A smoke.

## Verification

- Both services boot at their ports via `docker compose -f docker-compose.dotnet.yml up`.
- Parity tests match Python equivalents for canned inputs.

## Out of scope

- Inventory MCP server — that stays in Python at `agents/mcp/inventory_server.py` and is called by both stacks.
