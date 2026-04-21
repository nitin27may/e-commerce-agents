# Chapter 02 — Adding Tools

## Goal

Teach `@tool` (Python) / `AIFunctionFactory.Create` (.NET). Show the tool-calling loop by example: the LLM decides when to call the tool and incorporates the result.

## Article mapping

- **Supersedes**: [Part 3 — Building Domain-Specific Tools](https://nitinksingh.com/posts/building-domain-specific-tools--giving-agents-real-capabilities/)
- **New slug**: `/posts/maf-v1-tools/`

## Teaching strategy

- [x] Refactor excerpt — start with Ch01 and add one canned weather tool. Then point forward at production tools in `agents/product_discovery/tools.py:15` for real-world patterns (DB access, validation, error handling).

## Deliverables

### `python/`
- `main.py` — adds a `get_weather(city: str)` tool returning canned data, calls the agent.
- `tests/test_add_tools.py` — ≥ 3 tests: happy path, LLM skipped tool when not needed, tool invoked with expected args.

### `dotnet/`
- `Program.cs` — same tool defined via `[Description]` attribute on a plain method, wrapped with `AIFunctionFactory.Create`.
- `tests/AddTools.Tests/` — xUnit equivalents.

### Article
- `README.md`: contrast decorator style (Python) vs attribute style (.NET); show the tool schema the LLM sees; tips for naming and documenting tools.

## Verification

- The agent invokes the tool when asked about weather, skips it for unrelated questions.
- Tests assert `tool_invoked == True` for the weather question, `False` otherwise.

## How this maps into the capstone

Production tool patterns at `agents/product_discovery/tools.py:15` (search_products, get_product_details, semantic_search) — all follow the same `@tool + Annotated` pattern.

## Out of scope

- Tool approval (covered in Ch05 middleware).
- MCP tools (Ch08).
- Declarative tool schemas (Ch19).
