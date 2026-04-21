# .NET Port 02 — ECommerceAgents.Orchestrator

## Goal

Port `agents/orchestrator/*` to `dotnet/src/ECommerceAgents.Orchestrator/` — the front-door HTTP API plus the intent router.

## Python source → .NET target

| Python | .NET |
|--------|------|
| `orchestrator/main.py` (FastAPI app) | `Program.cs` (minimal API) |
| `orchestrator/routes.py` (`/api/chat`, `/api/chat/stream`, auth, sessions, marketplace, cart, checkout, returns) | `ChatEndpoints.cs`, `CartEndpoints.cs`, `OrderEndpoints.cs`, `MarketplaceEndpoints.cs` |
| `orchestrator/agent.py` (`call_specialist_agent` tool) | `OrchestratorAgent.cs` using MAF `HandoffBuilder` (A2A-over-HTTP as transport) |
| `orchestrator/prompts.py` | reads YAML via shared `IPromptLoader` |
| Docker/health/lifecycle | `Dockerfile` + `ECommerceAgents.Orchestrator.csproj` wired into compose |

## HTTP contract

Listens on `:8080`. Endpoints must match Python byte-for-byte:

- `POST /api/chat` → `{"response": "...", "session_id": "..."}`
- `POST /api/chat/stream` → SSE stream of `data: {"delta": "..."}\n\n`
- `GET /api/users/me`, JWT login/refresh endpoints
- `GET /api/marketplace/agents`, `POST /api/marketplace/agents/{id}/request`, admin approve/deny
- Cart / checkout / orders / returns

## Test project

`dotnet/tests/ECommerceAgents.Orchestrator.Tests/` — xUnit + `WebApplicationFactory`. Minimum **12 tests**:

- Chat: happy path, streaming SSE delta order, unauthenticated rejection, handoff to specialist returns merged response.
- Auth: login roundtrip, refresh token roundtrip, expired token rejection.
- Cart/checkout: add item, apply coupon, checkout creates order.
- Marketplace: list agents, request access, admin approve.
- Handoff wiring: orchestrator chooses the correct specialist for canned intents.

## Verification

- `docker compose -f docker-compose.dotnet.yml up orchestrator` responds on `:8080` with the same API shape as the Python version.
- The existing Playwright suite at `web/e2e/` (pointed at the .NET backend via `NEXT_PUBLIC_BACKEND_STACK=dotnet`) passes.

## Out of scope

- Specialists themselves (landed one per subsequent sub-plan).
