# Auth Route Matrix

Which routes accept which credential. Built by reading the middleware and the route dependencies, not from
memory: `shared/auth.py::AgentAuthMiddleware` for the specialists, the `require_*` / `optional_auth`
dependencies in `orchestrator/routes/legacy.py` for the orchestrator, `auth_server/main.py` for the
auth-server, and the MCP servers' `server.py` / `auth.py`.

It is kept honest by a test. `agents/python/tests/auth_route_manifest.json` classifies every route on every
HTTP surface, and `tests/test_auth_route_matrix.py` enumerates the routes from the real apps and fails when a
route is added without being classified, when a classified route disappears, when an orchestrator route's
declared dependency disagrees with the manifest, or when this page stops listing a manifest route. Adding a
route therefore means adding a row here and an entry there. Scope: the Python stack. The .NET stack is
specified to behave the same (see the [parity matrix](parity-matrix.md)) but its routes are not enumerated by
this test.

## Credentials

| Credential | What it is | Validated by |
|---|---|---|
| User JWT | `Authorization: Bearer <token>` for a signed-in person. `AUTH_MODE=local`: HS256 with `JWT_SECRET`, `type=access`. `AUTH_MODE=oauth`: RS256 from the auth-server, `aud=ecommerce-orchestrator`, `scope=api:chat`, verified against its JWKS. | `require_auth` / `optional_auth` (`orchestrator/routes/legacy.py`); on a specialist, local mode only |
| Agent secret | `X-Agent-Secret`, the shared secret between services. `AUTH_MODE=local` only: under `oauth` a request carrying it is refused outright. | `AgentAuthMiddleware` |
| OAuth service token | `Authorization: Bearer <token>` from the auth-server's client-credentials grant. Specialists want `aud=ecommerce-agents`, `scope=agent:invoke`; each MCP server wants its own audience and scope (`mcp-product`/`mcp:product`, `mcp-inventory`/`mcp:inventory`). `AUTH_MODE=oauth` only. | `AgentAuthMiddleware` (specialists), `JwksTokenVerifier` (MCP) |
| Signed `X-User-Context` | A short-lived, audience-bound token the orchestrator mints to say *which user* a call is for (`A2A_IDENTITY`). It is identity, not authentication: it travels beside an agent secret or service token and never replaces one. | `shared/identity_token.py`, inside `AgentAuthMiddleware` |
| Client credentials | `client_id` + `client_secret` (basic or form) at the auth-server's token endpoint. | `auth_server/` |
| None | No credential. Some of these routes are still guarded by something other than a header (a rate limit, an unguessable token in the path); the Notes column says so. | |

A specialist route marked "Agent secret or service token, plus user identity" needs one of those credentials
and a user identity: the forwarded `X-User-Email` / `X-User-Role` / `X-Session-Id` headers, or a verified
`X-User-Context` token, per `A2A_IDENTITY` (`headers`, `both`, `token`). With neither, the caller is the
`system` role. In `local` mode a specialist also accepts a user JWT directly; in `oauth` mode it does not (a
user token has the wrong audience and scope).

Roles on user routes: `support` and `admin` are staff, `admin` alone is admin, `seller` and `admin` pass
`require_seller`. Customers and power users hold none of them.

## Orchestrator (:8080)

The orchestrator has no inter-agent entry: it does not read `X-Agent-Secret` or `X-User-Context`, and no
service token opens a route. Everything below is a user JWT or nothing. The `/docs`, `/docs/oauth2-redirect`,
`/redoc` and `/openapi.json` rows are FastAPI's own documentation routes; they are enabled and public.

| Route | Credential | Role | Notes |
|---|---|---|---|
| `DELETE /api/cart/coupon` | User JWT | any signed-in user |  |
| `DELETE /api/cart/items/{item_id}` | User JWT | any signed-in user |  |
| `DELETE /api/conversations/{conversation_id}` | User JWT | any signed-in user |  |
| `DELETE /api/user/memories/{memory_id}` | User JWT | any signed-in user |  |
| `GET /api/admin/audit` | User JWT | admin |  |
| `GET /api/admin/decisions` | User JWT | admin |  |
| `GET /api/admin/decisions/{decision_id}` | User JWT | admin |  |
| `GET /api/admin/hitl/requests` | User JWT | admin |  |
| `GET /api/admin/outbox/parked` | User JWT | admin |  |
| `GET /api/admin/requests` | User JWT | admin |  |
| `GET /api/admin/usage` | User JWT | admin |  |
| `GET /api/agents/stats` | User JWT | any signed-in user |  |
| `GET /api/approvals` | User JWT | support or admin |  |
| `GET /api/approvals/{approval_id}` | User JWT | support or admin |  |
| `GET /api/cart` | User JWT | any signed-in user |  |
| `GET /api/conversations` | User JWT | any signed-in user |  |
| `GET /api/conversations/{conversation_id}` | User JWT | any signed-in user |  |
| `GET /api/demo/tour` | None |  | Public like the storefront; 404 unless DEMO_TOUR_ENABLED. |
| `GET /api/marketplace/agents` | User JWT | any signed-in user |  |
| `GET /api/marketplace/my-agents` | User JWT | any signed-in user |  |
| `GET /api/me/approvals` | User JWT | any signed-in user | Own approvals only, status only. |
| `GET /api/me/notifications` | User JWT | any signed-in user |  |
| `GET /api/orchestration/modes` | None |  | Public mode list. |
| `GET /api/orchestration/modes/{name}/graph` | None |  | Public diagram source. |
| `GET /api/orders` | User JWT | any signed-in user |  |
| `GET /api/orders/{order_id}` | User JWT | any signed-in user |  |
| `GET /api/products` | User JWT, optional | anonymous allowed | Anonymous browse allowed; a present-but-invalid token is still 401. |
| `GET /api/products/{product_id}` | User JWT, optional | anonymous allowed | Anonymous browse allowed; a present-but-invalid token is still 401. |
| `GET /api/profile` | User JWT | any signed-in user |  |
| `GET /api/returns/{label_token}/label` | None |  | Unauthenticated; the label token in the path is the only guard. See Known issues. |
| `GET /api/runs` | User JWT | any signed-in user |  |
| `GET /api/runs/{run_id}/checkpoints` | User JWT | any signed-in user |  |
| `GET /api/runs/{run_id}/timeline` | User JWT | any signed-in user |  |
| `GET /api/seller/orders` | User JWT | seller or admin |  |
| `GET /api/seller/products` | User JWT | seller or admin |  |
| `GET /api/seller/proposals` | User JWT | seller or admin |  |
| `GET /api/seller/proposals/{proposal_id}` | User JWT | seller or admin |  |
| `GET /api/seller/stats` | User JWT | seller or admin |  |
| `GET /api/staff/returns/inspections` | User JWT | support or admin |  |
| `GET /api/user/memories` | User JWT | any signed-in user |  |
| `GET /docs` | None |  | FastAPI default; public. |
| `GET /docs/oauth2-redirect` | None |  | FastAPI default; public. |
| `GET /health` | None |  |  |
| `GET /openapi.json` | None |  | FastAPI default; public. |
| `GET /ready` | None |  |  |
| `GET /redoc` | None |  | FastAPI default; public. |
| `POST /api/admin/decisions/{decision_id}/reverse` | User JWT | admin |  |
| `POST /api/admin/events/simulate` | User JWT | admin | 404 for everyone unless AUTONOMY_SIMULATOR_ENABLED (checked before the role). |
| `POST /api/admin/hitl/requests/{request_id}/approve` | User JWT | support or admin | Staff, not admin only: calls the approvals decide function. |
| `POST /api/admin/hitl/requests/{request_id}/deny` | User JWT | support or admin | Staff, not admin only: calls the approvals decide function. |
| `POST /api/admin/requests/{request_id}/approve` | User JWT | admin |  |
| `POST /api/admin/requests/{request_id}/deny` | User JWT | admin |  |
| `POST /api/admin/returns/{return_id}/simulate-scan` | User JWT | admin | 404 for everyone unless RETURN_LIFECYCLE_ENABLED. |
| `POST /api/approvals/{approval_id}/decide` | User JWT | support or admin |  |
| `POST /api/approvals/{approval_id}/info` | User JWT | any signed-in user | In-service: only the approval's requester may answer. |
| `POST /api/auth/login` | None |  | Credentials in the body (brokered to the auth-server by the orchestrator under oauth). Rate limited. |
| `POST /api/auth/refresh` | None |  | Refresh token in the body. Rate limited. |
| `POST /api/auth/signup` | None |  | Credentials in the body. Per-address rate limit. Always creates a customer. |
| `POST /api/cart/coupon` | User JWT | any signed-in user |  |
| `POST /api/cart/items` | User JWT | any signed-in user |  |
| `POST /api/chat` | User JWT, optional | anonymous allowed | Anonymous callers reach product discovery only; identity is empty. Rate limited. |
| `POST /api/chat/stream` | User JWT, optional | anonymous allowed | Anonymous callers reach product discovery only; identity is empty. Rate limited. |
| `POST /api/checkout` | User JWT | any signed-in user |  |
| `POST /api/demo/reset` | User JWT | any signed-in user | In-handler: only the demo tour user; 404 unless DEMO_TOUR_ENABLED. |
| `POST /api/marketplace/request` | User JWT | any signed-in user |  |
| `POST /api/me/notifications/{notification_id}/read` | User JWT | any signed-in user |  |
| `POST /api/orchestration/compare` | User JWT | any signed-in user |  |
| `POST /api/orchestration/{run_id}/resume` | User JWT | support or admin | Staff, never the run's requester; reviewer role must meet the policy's level. |
| `POST /api/orders/{order_id}/cancel` | User JWT | any signed-in user |  |
| `POST /api/orders/{order_id}/return` | User JWT | any signed-in user |  |
| `POST /api/seller/proposals/run` | User JWT | seller or admin |  |
| `POST /api/seller/proposals/{proposal_id}/decide` | User JWT | seller or admin |  |
| `POST /api/staff/returns/{return_id}/inspect` | User JWT | support or admin | 404 unless RETURN_LIFECYCLE_ENABLED. |
| `PUT /api/cart/address` | User JWT | any signed-in user |  |
| `PUT /api/cart/items/{item_id}` | User JWT | any signed-in user |  |

## Specialists (:8081-8085, same shape for all five)

`AgentAuthMiddleware` wraps the whole app. Only `/health`, `/ready` and `/.well-known/agent-card.json` skip it
(`shared/auth.py::PUBLIC_PATHS`), so FastAPI's own `/docs`, `/redoc` and `/openapi.json` need a credential here
even though they are public on the orchestrator.

| Route | Credential | Notes |
|---|---|---|
| `GET /.well-known/agent-card.json` | None | Public card; lists only tools the customer role may call. |
| `GET /docs` | Agent secret or service token, plus user identity | FastAPI default, behind the middleware: needs a credential. |
| `GET /docs/oauth2-redirect` | Agent secret or service token, plus user identity | FastAPI default, behind the middleware: needs a credential. |
| `GET /health` | None | Liveness. |
| `GET /openapi.json` | Agent secret or service token, plus user identity | FastAPI default, behind the middleware: needs a credential. |
| `GET /ready` | None | Readiness; cached, no detail beyond status. |
| `GET /redoc` | Agent secret or service token, plus user identity | FastAPI default, behind the middleware: needs a credential. |
| `POST /a2a/v1` | Agent secret or service token, plus user identity | A2A 1.0 JSON-RPC. Same credentials and forwarded identity as the legacy pair. |
| `POST /message:send` | Agent secret or service token, plus user identity | Legacy A2A. Needs a credential plus the forwarded user identity (headers or X-User-Context). |
| `POST /message:stream` | Agent secret or service token, plus user identity | Legacy A2A SSE. Same as /message:send. |

## Auth-server (:8090, `AUTH_MODE=oauth` only)

| Route | Credential | Notes |
|---|---|---|
| `GET /.well-known/jwks.json` | None | Public keys only: the active key plus rotated-out keys inside their window. |
| `GET /.well-known/oauth-authorization-server` | None | RFC 8414 metadata. |
| `GET /health` | None |  |
| `POST /oauth/register` | Auth-server bearer, scope `client:register` | 403 unless AUTH_ALLOW_DYNAMIC_REGISTRATION; then a bearer token from this auth-server with scope client:register. |
| `POST /oauth/token` | Client credentials | client_secret_basic or client_secret_post; the password grant also needs the user's password, the refresh grant a refresh token. |

## MCP servers (`mcp-product`, `mcp-inventory`)

Same two modes for both. With `MCP_AUTH_ENABLED=true` the SDK's bearer middleware guards the MCP endpoint and
the protected-resource metadata is published. With it false there is no check at all.

With `MCP_AUTH_ENABLED=true`:

| Route | Credential | Notes |
|---|---|---|
| `ANY /mcp` | OAuth service token |  |
| `GET /.well-known/oauth-protected-resource/mcp` | None |  |

With `MCP_AUTH_ENABLED=false` (the default):

| Route | Credential | Notes |
|---|---|---|
| `ANY /mcp` | None | MCP_AUTH_ENABLED is false: the endpoint is open to anything that can reach it. |

## Known issues

These came out of reading the code for this page. The return-label match is fixed; the rest are not.

- **The return-label route is a capability URL.** `GET /api/returns/{label_token}/label` takes no credential;
  the 12-character token in the path is the only guard. It used to match with `LIKE '%<token>%'`, so a `%` or
  `_` segment returned another customer's label (fixed in #167: the segment must be exactly 12 lowercase hex
  characters and the lookup is an equality match, on both stacks). Still open: the token is 48 bits
  (`uuid4().hex[:12]`) and unknown-token 404s are not rate limited.
- **`MCP_AUTH_ENABLED=false` leaves `/mcp` open.** That is the documented default for local use; anything that can
  reach the port can call the tools. Network policy is the only guard until the flag is on.
- **Under `A2A_IDENTITY=both` (the default) a caller holding only the agent secret can pick its user** by
  forging the forwarded headers. `A2A_IDENTITY=token` with a distinct `A2A_IDENTITY_KEY` closes it
  (see [a2a.md](a2a.md)).
- **`POST /api/chat*` accepts anonymous callers** by design (product discovery for logged-out shoppers), with an
  empty identity and a per-address rate limit. A present-but-invalid token is still a 401, not a downgrade to
  anonymous.
- **A 404 is part of some guards.** The simulator and return-lifecycle routes answer 404 to everyone, admin
  included, unless their flag is on, so a 401 or 403 cannot reveal that they exist.
