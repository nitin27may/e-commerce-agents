# Agent Security Audit Matrix

Per-agent snapshot of the current security posture and the remaining hardening targets.
This matrix is the anchor reference for [`docs/security-guide.md`](security-guide.md) and
[`docs/agent-quality.md`](agent-quality.md).

**Legend**

| Symbol | Meaning |
|--------|---------|
| Done | Implemented and verified in tests |
| Partial | Present but incomplete (noted inline) |
| Target | Not yet implemented; tracked as a follow-up |

---

## Dimensions

| # | Dimension | What it covers |
|---|-----------|---------------|
| 1 | **Injection defense — inbound** | `InjectionDetectionChatMiddleware` scanning user messages before they reach the model |
| 2 | **Injection defense — stored** | `OutputSanitizationMiddleware` neutralizing tool results that carry user-generated text before it re-enters the model. **Python-only** — there is no .NET equivalent; the .NET stack has no stored-injection defense at all |
| 3 | **Role enforcement** | `@requires_role` / `ensure_role` checks on destructive or privileged tools |
| 4 | **Eval dataset** | Named dataset file under `agents/python/evals/datasets/` |
| 5 | **Red-team coverage** | Attack vectors from `red_team.json` that target this agent |

---

## Matrix

### Orchestrator (`orchestrator`, port 8080)

**Role**: Front door. Routes all user requests to specialist agents via `call_specialist_agent`. No domain tools; LLM output is a routing instruction, not user-facing data.

| Dimension | Status | Detail |
|-----------|--------|--------|
| Inbound injection detection | Done | `InjectionDetectionChatMiddleware` in `build_specialist_middleware()` |
| Stored-content sanitization | N/A | No tools that return user-generated free text |
| Role enforcement | N/A | No domain tools; routing only |
| Eval dataset | Done | `datasets/orchestrator_routing.json` — 6 intent/routing cases |
| Red-team coverage | Done | `red_team.json`: jailbreak (DAN prompt), instruction-override injection |
| Auth mode | Done | Brokers `local` HS256 / `oauth` RS256 user login (`/api/auth/login`, `/api/auth/refresh`); relays inter-agent service tokens to specialists in `oauth` mode |

**Target**: None — current posture matches the agent's narrow scope.

---

### Product Discovery (`product-discovery`, port 8081)

**Role**: Natural language product search, semantic similarity, price history, trending products.

| Dimension | Status | Detail |
|-----------|--------|--------|
| Inbound injection detection | Done | `InjectionDetectionChatMiddleware` |
| Stored-content sanitization | Done | `search_products`, `get_product_details`, `find_similar_products`, `semantic_search`, `get_trending_products` all covered in `guardrails/config.py:SANITIZE_TOOLS` |
| Role enforcement | N/A | All current tools are public; no privileged operations |
| Eval dataset | Done | `datasets/product_discovery.json` |
| Red-team coverage | Done | `red_team.json`: embedded-token injection (`PWNED-1337`) via product search |
| Auth mode | Done | Inter-agent: `local` shared secret / `oauth` `agent:invoke` service token (unchanged either way from this agent's perspective). MCP (when `MCP_ENABLED=true`): `oauth`+`MCP_AUTH_ENABLED=true` attaches a separate `mcp:product` service token via `header_provider` |

**Target**: If seller-only product-management tools are added (create/update product), gate them with `@requires_role("seller", "admin")`.

---

### Order Management (`order-management`, port 8082)

**Role**: Order tracking, cancellation, modification, returns, refunds, cart operations.

| Dimension | Status | Detail |
|-----------|--------|--------|
| Inbound injection detection | Done | `InjectionDetectionChatMiddleware` |
| Stored-content sanitization | Done | `get_order_details`, `get_user_orders` covered in `SANITIZE_TOOLS` (fields: `note`, `notes`, `reason`) |
| Role enforcement | Done | SQL-layer ownership filter (`WHERE u.email = $2`) plus `@requires_role("customer", "seller", "admin")` on `cancel_order`/`modify_order` (both stacks — Python decorator, .NET `RoleGuard.Ensure` in `OrderTools.cs`); `process_refund` (Python-only — no .NET port) carries the same decorator in `shared/tools/return_tools.py` |
| Eval dataset | Done | `datasets/order_management.json` |
| Red-team coverage | Done | `red_team.json`: role escalation (fetch all users' orders; expose another user's address) |
| Auth mode | Done | Inter-agent: `local` shared secret / `oauth` `agent:invoke` service token — no MCP integration for this agent |

---

### Pricing & Promotions (`pricing-promotions`, port 8083)

**Role**: Coupon validation, cart optimization, loyalty discounts, bundle deals, active promotions.

| Dimension | Status | Detail |
|-----------|--------|--------|
| Inbound injection detection | Done | `InjectionDetectionChatMiddleware` |
| Stored-content sanitization | N/A | Tool outputs are structured/numeric (prices, discount amounts, eligibility flags) |
| Role enforcement | Partial | Seller-specific revenue tools in `shared/tools/seller_tools.py` all carry `@requires_role("seller", "admin")`; the pricing agent does not currently expose those tools, but any future addition must carry the decorator |
| Eval dataset | Done | `datasets/pricing_promotions.json` |
| Red-team coverage | Done | `red_team.json`: role escalation (impersonate seller to access revenue/payout data) |
| Auth mode | Done | Inter-agent: `local` shared secret / `oauth` `agent:invoke` service token — no MCP integration for this agent |

**Target**: If `get_seller_revenue` or similar tools are wired into this agent, they are already decorated; no further action required. Add `ensure_role` guard in any new tool that touches per-seller financials.

---

### Review & Sentiment (`review-sentiment`, port 8084)

**Role**: Review analysis, sentiment breakdown, fake-review detection, seller response drafting, cross-product comparison.

| Dimension | Status | Detail |
|-----------|--------|--------|
| Inbound injection detection | Done | `InjectionDetectionChatMiddleware` — highest-risk agent for inbound injection because user input can request review text |
| Stored-content sanitization | Done | All read tools (`get_product_reviews`, `analyze_sentiment`, `compare_reviews`, `detect_fake_reviews`, `get_review_trends`) covered in `SANITIZE_TOOLS` with explicit field allowlists |
| Role enforcement | Done | `draft_seller_response` gated `@requires_role("seller", "admin")` (Python) / `RoleGuard.Ensure` in `ReviewTools.cs` (.NET) |
| Eval dataset | Done | `datasets/review_sentiment.json` |
| Red-team coverage | Done | `red_team.json`: two injection attacks — direct system-prompt dump via review request; indirect via embedded "follow it" instruction in review content |
| Auth mode | Done | Inter-agent: `local` shared secret / `oauth` `agent:invoke` service token — no MCP integration for this agent |

---

### Inventory & Fulfillment (`inventory-fulfillment`, port 8085)

**Role**: Stock checking, warehouse availability, shipping estimation, carrier comparison, backorder placement.

| Dimension | Status | Detail |
|-----------|--------|--------|
| Inbound injection detection | Done | `InjectionDetectionChatMiddleware` |
| Stored-content sanitization | N/A | Tool outputs are structured (quantities, ETAs, carrier rates) |
| Role enforcement | Done | `place_backorder` and `calculate_fulfillment_plan` gated `@requires_role("seller", "admin")` (Python) / `RoleGuard.Ensure` in `InventoryTools.cs` (.NET) |
| Eval dataset | Done | `datasets/inventory_fulfillment.json` |
| Red-team coverage | Done | `red_team.json`: injection via stock-check request attempting secret exfiltration (`AGENT_SHARED_SECRET`) |
| Auth mode | Done | Inter-agent: `local` shared secret / `oauth` `agent:invoke` service token (unchanged either way from this agent's perspective). MCP (when `MCP_ENABLED=true`): `oauth`+`MCP_AUTH_ENABLED=true` attaches a separate `mcp:inventory` service token via `header_provider` |

---

### MCP Servers (`mcp-product` :9000, `mcp-inventory` :9001) — Python

**Role**: Standalone, independently publishable MCP servers (`packages/mcp-product`, `packages/mcp-inventory`) exposing product/inventory data over MCP streamable HTTP. Consumed by `product-discovery`/`inventory-fulfillment` when `MCP_ENABLED=true`.

| Dimension | Status | Detail |
|-----------|--------|--------|
| Auth mode | Done | Default: unauthenticated (`MCP_AUTH_ENABLED=false`). Optional OAuth 2.1 resource-server mode: vendored `JwksTokenVerifier` per package (not shared — each is an isolated uv workspace member), `aud=mcp-product`/`mcp-inventory`, `scope=mcp:product`/`mcp:inventory`. Unauthenticated/wrong-scope calls get `401` + `WWW-Authenticate`; `GET /.well-known/oauth-protected-resource/mcp` is public |
| Role enforcement | N/A | Resource servers have no end-user role concept — access is gated purely by the calling specialist's own service token scope |
| Eval dataset | N/A | No LLM in this surface — pure data tools |
| Red-team coverage | N/A | Not part of the LLM red-team suite; covered by `packages/*/tests/test_auth.py`'s reject-shapes instead |

**Target**: None open — `MCP_AUTH_ENABLED=false` (default) is a byte-for-byte regression guard against pre-existing behavior; `=true` is fully unit- and integration-tested (real RS256 crypto, real in-process AS).

---

### .NET Port — orchestrator, specialists, MCP host

**Role**: Parity .NET implementation (`agents/dotnet/`) of the same platform. Shares the identical self-hosted Authorization Server (Python, `auth_server/`) — both stacks are never meant to run simultaneously against the same AS instance in this repo's compose setup, but the AS itself has no stack-specific logic.

| Dimension | Status | Detail |
|-----------|--------|--------|
| Auth mode (orchestrator) | Done | Same `local`/`oauth` login/refresh broker as Python, via `AuthRoutes.cs` + `AuthServerClient`. Verified live in Docker (`docker-compose.dotnet.yml`), including real RBAC (customer 403 / admin 200) under `oauth` mode |
| Auth mode (inter-agent, A2A) | Done | `A2AClient.cs` attaches an `agent:invoke` service token in `oauth` mode instead of `X-Agent-Secret`; `AgentAuthMiddleware.cs` validates it. One middleware class serves both the orchestrator's user-token role and specialists' service-token role, disambiguated by an explicit `isOrchestrator` flag set at startup (`UseAgentAuth(isOrchestrator:)`) — Python splits these into two separate code paths instead |
| Auth mode (MCP) | Done | `McpEndpoints.cs` gates `POST /mcp/tools/{toolName}` on `MCP_AUTH_ENABLED`, reusing the same `JwtTokenService.ValidateOAuth`/`JwksKeyProvider` as the A2A path; hand-rolled `GET /.well-known/oauth-protected-resource` (no `AddMicrosoftIdentityWebApi`) |
| Deployability | Done | Each of the 5 specialists and `ECommerceAgents.Mcp` has its own `Dockerfile` (mirroring the orchestrator's), and `docker-compose.dotnet.yml` composes all of them (`mcp-inventory` under the `mcp` profile). Live-verified: full stack up (`--profile agents --profile mcp --profile seed`), all 8 services healthy, real chat turn (real Azure OpenAI) routed via `AGENT_REGISTRY` to a real specialist and back. One remaining asymmetry: no .NET equivalent of Python's `mcp-product` server exists yet — only `mcp-inventory` has a .NET port |

**Target**: None open for deployability. Building a .NET `mcp-product`-equivalent server (parity with Python's second MCP server) remains a separate, unscoped enhancement.

---

## Summary Table

| Agent | Inbound Inject. | Output Sanitize | Role Enforce | Eval Dataset | Red-team | Auth Mode |
|-------|:-----------:|:-----------:|:-----------:|:-----------:|:-----------:|:-----------:|
| orchestrator | Done | N/A | N/A | Done | Done | Done |
| product-discovery | Done | Done | N/A | Done | Done | Done |
| order-management | Done | Done | Done | Done | Done | Done |
| pricing-promotions | Done | N/A | Partial | Done | Done | Done |
| review-sentiment | Done | Done | Done | Done | Done | Done |
| inventory-fulfillment | Done | N/A | Done | Done | Done | Done |
| mcp-product / mcp-inventory | N/A | N/A | N/A | N/A | N/A | Done |
| .NET port | — | — | Done (5 tools, `RoleGuard`) | — | — | Done (auth code + deployability) |

**Open items (priority order)**

1. `pricing-promotions` — no open action today; the `Partial` rating is a forward-looking note (any future per-seller financial tool must carry the same role decorator), not a gap in shipped code.
2. `.NET port` — no .NET equivalent of Python's `mcp-product` server exists (only `mcp-inventory` is ported) — a separate, unscoped enhancement, not a gap in anything shipped.

---

## Related documents

- [`docs/security-guide.md`](security-guide.md) — threat model, guardrails architecture, auth flow, Azure AI Content Safety option
- [`docs/agent-quality.md`](agent-quality.md) — eval philosophy, datasets, CI gate
- [`docs/maf-best-practices.md`](maf-best-practices.md) — MAF idioms and patterns used across all agents
