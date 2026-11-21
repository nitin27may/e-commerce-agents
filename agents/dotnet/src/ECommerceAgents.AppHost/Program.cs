using ECommerceAgents.AppHost;

// Run with:  dotnet run --project agents/dotnet/src/ECommerceAgents.AppHost
// Python backend instead:  AgentStack=python dotnet run --project agents/dotnet/src/ECommerceAgents.AppHost
// See docs/deployment.md ("Running under Aspire"). docker compose remains the reference path.
var builder = DistributedApplication.CreateBuilder(args);

AppHostModel.Configure(builder);

builder.Build().Run();
