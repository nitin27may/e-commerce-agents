using ECommerceAgents.Orchestrator.Agent;
using ECommerceAgents.Orchestrator.Routes;
using ECommerceAgents.Shared.A2A;
using ECommerceAgents.Shared.Auth;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Prompts;
using ECommerceAgents.Shared.Telemetry;
using Microsoft.Agents.AI;

var builder = WebApplication.CreateBuilder(args);

var settings = AgentSettingsLoader.Load(builder.Configuration);
AgentSettingsValidator.Validate(
    settings,
    LoggerFactory.Create(lb => lb.AddConsole()).CreateLogger("SettingsValidator")
);
builder.Services.AddSingleton(settings);
builder.Services.AddSingleton(new DatabasePool(settings));
builder.Services.AddSingleton(new JwtTokenService(settings));
builder.Services.AddSingleton(new PromptLoader(PromptsRoot()));
builder.Services.AddAgentTelemetry(settings);

builder.Services.AddHttpClient();
builder.Services.AddSingleton(sp =>
{
    var http = sp.GetRequiredService<IHttpClientFactory>().CreateClient("a2a");
    http.Timeout = TimeSpan.FromSeconds(30);
    return new A2AClient(http, settings, sp.GetRequiredService<Microsoft.Extensions.Logging.ILogger<A2AClient>>());
});
builder.Services.AddSingleton<OrchestratorTools>();
builder.Services.AddSingleton<AIAgent>(sp =>
{
    var prompts = sp.GetRequiredService<PromptLoader>();
    var tools = sp.GetRequiredService<OrchestratorTools>();
    return OrchestratorAgentFactory.Create(settings, prompts, tools);
});

var app = builder.Build();

app.UseAgentAuth();

app.MapGet("/", () => Results.Ok(new { status = "ok", service = "orchestrator", port = 8080 }));
app.MapGet("/health", () => Results.Ok(new { healthy = true }));

app.MapAuthRoutes();
app.MapChatRoutes();

var urls = Environment.GetEnvironmentVariable("ASPNETCORE_URLS");
app.Run(string.IsNullOrWhiteSpace(urls) ? "http://0.0.0.0:8080" : urls);


static string PromptsRoot()
{
    var dir = new DirectoryInfo(AppContext.BaseDirectory);
    while (dir is not null && !Directory.Exists(Path.Combine(dir.FullName, "agents", "python", "config", "prompts")))
    {
        dir = dir.Parent;
    }
    return dir is not null
        ? Path.Combine(dir.FullName, "agents", "python", "config", "prompts")
        : Path.Combine(AppContext.BaseDirectory, "config", "prompts");
}
