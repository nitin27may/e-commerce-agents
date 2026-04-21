# .NET Port 00 — Solution Scaffold

## Goal

Stand up the .NET solution skeleton so every subsequent module has somewhere to land.

## Deliverables

- `dotnet/ECommerceAgents.sln`
- `dotnet/Directory.Packages.props` (central package management) pinning:
  - `Microsoft.Agents.AI` (latest prerelease)
  - `Microsoft.Agents.AI.OpenAI`
  - `Azure.AI.OpenAI`
  - `Npgsql`
  - `StackExchange.Redis`
  - `OpenTelemetry.Exporter.OpenTelemetryProtocol`
  - `OpenTelemetry.Extensions.Hosting`
  - `Microsoft.IdentityModel.Tokens` / `System.IdentityModel.Tokens.Jwt`
  - `YamlDotNet`
  - `xUnit`, `FluentAssertions`, `Moq`, `Testcontainers.PostgreSql`
- Empty projects under `dotnet/src/`:
  - `ECommerceAgents.Shared`
  - `ECommerceAgents.Orchestrator`
  - `ECommerceAgents.ProductDiscovery`
  - `ECommerceAgents.OrderManagement`
  - `ECommerceAgents.PricingPromotions`
  - `ECommerceAgents.ReviewSentiment`
  - `ECommerceAgents.InventoryFulfillment`
- `dotnet/tests/TestFixtures` project with a shared Postgres testcontainer fixture and a fake `IChatClient`.
- `docker-compose.dotnet.yml` — same Postgres/Redis/Aspire/web, .NET backend containers built from each project's Dockerfile.
- `scripts/dev-dotnet.sh` (stub) — mirrors `scripts/dev.sh` for the .NET stack.

## Verification

- `dotnet build dotnet/ECommerceAgents.sln` succeeds.
- `docker compose -f docker-compose.dotnet.yml config` validates.
- All projects listed above are registered in `dotnet list sln`.

## Out of scope

- Any business logic (comes in `01-shared.md` and subsequent sub-plans).
- Dockerfiles per agent (added in `02-orchestrator.md` and later, once there's something to containerize).
