using ECommerceAgents.Mcp;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Data;

var builder = WebApplication.CreateBuilder(args);

var settings = AgentSettingsLoader.Load(builder.Configuration);
builder.Services.AddSingleton(settings);
builder.Services.AddSingleton(new DatabasePool(settings));

var app = builder.Build();
app.MapMcpEndpoints();
app.Run(Environment.GetEnvironmentVariable("ASPNETCORE_URLS") ?? "http://0.0.0.0:9000");
