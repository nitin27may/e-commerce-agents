# .NET Port Sub-Plans

Port `agents/` (Python backend) to `dotnet/src/` module by module. Reuse the Next.js frontend and all shared infrastructure (Postgres, Redis, Aspire, seed data, prompt YAML) unchanged.

## Order

1. [`00-scaffold.md`](./00-scaffold.md) — solution skeleton, packages, docker-compose.
2. [`01-shared.md`](./01-shared.md) — `ECommerceAgents.Shared` (auth, telemetry, config, prompt loader, context providers).
3. [`02-orchestrator.md`](./02-orchestrator.md) — `ECommerceAgents.Orchestrator` (ASP.NET Core minimal API + handoff).
4. [`03-product-discovery.md`](./03-product-discovery.md) — specialist at `:8081`.
5. [`04-order-management.md`](./04-order-management.md) — specialist at `:8082`.
6. [`05-pricing-promotions.md`](./05-pricing-promotions.md) — specialist at `:8083`.
7. [`06-review-sentiment-inventory.md`](./06-review-sentiment-inventory.md) — two smallest specialists combined.

Each sub-plan lists its Python source, .NET target, test project with minimum test count, and NuGet packages.

## Cross-stack parity

A dedicated `dotnet/tests/ECommerceAgents.Parity.Tests/` runs canned conversations against both backends and asserts equivalent observable outputs. This is our insurance that the port stays faithful.
