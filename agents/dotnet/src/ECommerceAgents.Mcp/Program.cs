using ECommerceAgents.Mcp;
using ECommerceAgents.Shared.Auth;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Data;
using ModelContextProtocol.AspNetCore;

var builder = WebApplication.CreateBuilder(args);

var settings = AgentSettingsLoader.Load(builder.Configuration);
builder.Services.AddSingleton(settings);
builder.Services.AddSingleton(new DatabasePool(settings));
builder.Services.AddSingleton(new JwtTokenService(settings));
builder.Services.AddHttpClient<JwksKeyProvider>();

// Real MCP protocol (JSON-RPC over streamable HTTP), not a REST imitation —
// parity with the Python FastMCP server this mirrors. Stateless mode: no
// server-held session state between calls, matching how the REST version
// (and every other agent in this repo) already treats each request
// independently.
builder.Services.AddMcpServer()
    .WithHttpTransport(options => options.Stateless = true)
    .WithToolsFromAssembly();

var app = builder.Build();
app.UseMcpAuthGate();
app.MapMcpHealthEndpoints();
app.MapMcp(McpEndpoints.McpRoutePrefix);
app.Run(Environment.GetEnvironmentVariable("ASPNETCORE_URLS") ?? "http://0.0.0.0:9001");
