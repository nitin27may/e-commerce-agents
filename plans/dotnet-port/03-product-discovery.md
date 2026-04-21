# .NET Port 03 — ECommerceAgents.ProductDiscovery

## Goal

Port `agents/product_discovery/*` to `dotnet/src/ECommerceAgents.ProductDiscovery/`.

## Python source → .NET target

| Python | .NET |
|--------|------|
| `product_discovery/agent.py` | `ProductDiscoveryAgent.cs` using MAF `ChatClientAgent` |
| `product_discovery/tools.py` (`search_products`, `get_product_details`, `semantic_search`, etc.) | `ProductDiscoveryTools.cs` with `[Description]`-attributed methods wrapped via `AIFunctionFactory.Create` |
| `product_discovery/prompts.py` | reads YAML via shared `IPromptLoader` |
| `product_discovery/main.py` | `Program.cs` (minimal API on `:8081`) |
| A2A `/message:send` + `.well-known/agent-card.json` | A2A endpoint handlers |

## Test project

`dotnet/tests/ECommerceAgents.ProductDiscovery.Tests/` — minimum **10 tests**:

- `search_products`: filter by category, by price range, pagination, empty result.
- `get_product_details`: existing + missing product.
- `semantic_search`: vector similarity returns top-K, respects min-similarity threshold.
- Agent: system prompt loaded; tool invoked when question mentions products; A2A endpoint accepts valid secret header.

## Verification

- Smoke: cross-stack parity test sends the same canned question to Python `:8081` and .NET `:8081`; both return products with the same IDs.

## Out of scope

- Changing product schema — shared with Python.
- Re-indexing embeddings — script stays in Python (`scripts/generate_embeddings.py`).
