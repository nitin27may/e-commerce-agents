# .NET Port 01 — ECommerceAgents.Shared

## Goal

Port `agents/shared/*` to `dotnet/src/ECommerceAgents.Shared/` — the cross-cutting library every specialist depends on.

## Python source → .NET target

| Python module | .NET class |
|---------------|-----------|
| `shared/config.py` (Pydantic Settings) | `EcommerceSettings` (bound from `IConfiguration`) + `AddEcommerceConfiguration()` extension. Reads existing env vars; accepts `AZURE_OPENAI_KEY` and `AZURE_OPENAI_API_KEY` aliases. |
| `shared/auth.py` (`AgentAuthMiddleware`) | `AgentAuthMiddleware` ASP.NET Core middleware. |
| `shared/context.py` (ContextVars) | `AsyncLocal<UserContext>` via `IUserContextAccessor`. |
| `shared/context_providers.py` (`ECommerceContextProvider`) | `EcommerceContextProvider` implementing MAF `ContextProvider`. |
| `shared/prompt_loader.py` | `IPromptLoader` reading the SAME `agents/config/prompts/*.yaml` files via `YamlDotNet`. |
| `shared/telemetry.py` | `AddEcommerceTelemetry()` extension wiring OpenTelemetry with GenAI semantic conventions. |
| `shared/db.py` | `INpgsqlConnectionFactory` + connection pool, same conn string from `DATABASE_URL`. |
| `shared/agent_host.py` | **Skipped** — MAF native execution replaces the custom tool loop in .NET from day one. |
| `shared/usage_db.py` | `UsageLogger` writing to the existing `usage_logs` table. |

## Test project

`dotnet/tests/ECommerceAgents.Shared.Tests/` — xUnit + FluentAssertions. Minimum **15 tests**:

- Config binding (5): `LLM_PROVIDER=openai`, `=azure` with both key-name aliases, missing-required-field fails fast, Azure deployment fallback, `MAF_*` flags default correctly.
- Auth middleware (3): JWT path, inter-agent secret path, rejection on missing credentials.
- Context providers (3): state key present, chained providers merge, DB-failure degrades gracefully.
- Prompt loader (2): composes from fragments; unknown role raises cleanly.
- Telemetry (2): span emits with GenAI attrs; trace context propagates across scoped call.

## Verification

- `dotnet test dotnet/tests/ECommerceAgents.Shared.Tests/` green with coverage ≥ 80% on `ECommerceAgents.Shared`.
- Running a smoke integration (a one-line test that reads a prompt via both Python and .NET loaders) produces byte-identical output.

## Out of scope

- HTTP routes (live in Orchestrator).
- Agent instantiation (lives in specialist projects).
