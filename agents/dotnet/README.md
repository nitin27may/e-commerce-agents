# ECommerceAgents — .NET Backend

A complete, working .NET / C# implementation of the platform, built with Microsoft Agent
Framework alongside the Python backend at `../python/`. Development here is Python-first — see
[`../../docs/parity-matrix.md`](../../docs/parity-matrix.md) for exactly which concepts are at
full parity today versus still on the .NET backlog.

Both stacks share:
- Postgres schema at `../../docker/postgres/init.sql`
- Prompt YAML at `../python/config/prompts/`
- Seed data produced by `../../scripts/seed.py`
- Next.js frontend at `../../web/` (selects backend via `NEXT_PUBLIC_BACKEND_STACK=python|dotnet`)

## Projects

| Project | Port | Description |
|---------|------|-------------|
| `src/ECommerceAgents.Shared` | library | Config, auth, telemetry, context providers, prompt loader, DB pool |
| `src/ECommerceAgents.Orchestrator` | 8080 | ASP.NET Core minimal API; HandoffBuilder-based routing |
| `src/ECommerceAgents.ProductDiscovery` | 8081 | Product catalog agent |
| `src/ECommerceAgents.OrderManagement` | 8082 | Order lifecycle agent |
| `src/ECommerceAgents.PricingPromotions` | 8083 | Pricing + coupon agent |
| `src/ECommerceAgents.ReviewSentiment` | 8084 | Review summarization agent |
| `src/ECommerceAgents.InventoryFulfillment` | 8085 | Inventory + shipping agent |

Test projects mirror each under `tests/`.

## Build + test

```bash
cd agents/dotnet
dotnet restore
dotnet build
dotnet test
```

## Run the full .NET stack

```bash
# From repo root — one-command helper (builds, seeds, and starts everything):
./scripts/dev.sh --dotnet

# Or plain Docker Compose. Every app service (seeder, agents, MCP host,
# frontend) is gated behind a profile — only db/redis/aspire start
# unconditionally — so `up --build` with no --profile flags only brings up
# infrastructure. Include all four profiles to get the full app:
docker compose -f docker-compose.dotnet.yml \
  --profile seed --profile agents --profile mcp --profile frontend up --build
```

The Next.js frontend at `http://localhost:3000` will talk to the .NET orchestrator at `:8080` when `NEXT_PUBLIC_BACKEND_STACK=dotnet`.

## Central package management

All package versions live in `Directory.Packages.props` at this folder root. Individual `.csproj` files use `<PackageReference Include="..." />` without a `Version=` attribute.

## Status

The .NET backend is functionally complete for the core domain: all six specialist agents plus an
MCP server (`ECommerceAgents.Mcp`, real JSON-RPC over streamable HTTP via the official
`ModelContextProtocol.AspNetCore` SDK) are implemented, along with a shared layer covering A2A
client/host, JWT auth middleware, tool audit logging, PII redaction, checkpoint storage
(in-memory, file, and Postgres backends), declarative workflow primitives, and config validation.

It is **not** at full parity with the Python backend — see
[`../../docs/parity-matrix.md`](../../docs/parity-matrix.md) for the honest, per-concept
breakdown. The biggest current gaps: agent middleware/context providers exist but aren't wired
into any live agent, specialist agents have no streaming (`/message:stream`) endpoint, and
there's no output sanitization/injection detection, step recorder, or `WorkflowBuilder`-based
fan-out.

Eight test projects mirror the source structure — one per agent plus Shared and MCP — with 349 test methods covering tools, middleware, auth, and A2A protocol behavior (verified directly: `dotnet test ECommerceAgents.sln`). The same PostgreSQL schema and A2A wire format are used across both stacks; you can point the frontend at either backend by setting `NEXT_PUBLIC_BACKEND_STACK=dotnet`.

Enhancement plans are tracked in [`.claude/plans/enhancements/`](../../.claude/plans/enhancements/).
