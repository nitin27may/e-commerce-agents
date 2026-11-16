using ECommerceAgents.Orchestrator.Agent;
using ECommerceAgents.Orchestrator.Approvals;
using ECommerceAgents.Orchestrator.Routes;
using ECommerceAgents.Shared.Orchestration;
using ECommerceAgents.Orchestrator.Modes;
using ECommerceAgents.Shared.A2A;
using ECommerceAgents.Shared.Auth;
using ECommerceAgents.Shared.Checkpoints;
using Microsoft.Agents.AI.Workflows;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.RateLimiting;
using ECommerceAgents.Shared.Sessions;
using ECommerceAgents.Shared.ContextProviders;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Health;
using ECommerceAgents.Shared.Middleware;
using ECommerceAgents.Shared.Prompts;
using ECommerceAgents.Shared.Telemetry;
using Microsoft.Agents.AI;
using System.Text.Json;

var builder = WebApplication.CreateBuilder(args);

builder.Services.ConfigureHttpJsonOptions(opts =>
{
    opts.SerializerOptions.PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower;
    opts.SerializerOptions.DictionaryKeyPolicy = JsonNamingPolicy.SnakeCaseLower;
    opts.SerializerOptions.PropertyNameCaseInsensitive = true;
});

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
builder.Services.AddSingleton<UsageRecorder>();

builder.Services.AddHttpClient();
builder.Services.AddHttpClient<JwksKeyProvider>();
builder.Services.AddHttpClient<AuthServerClient>();
// The specialist client carries X-Agent-Secret and the forwarded user identity on every request, so it must
// never follow a redirect: a 3xx would replay those headers against whatever origin it names (the A2A 1.0
// client also refuses any 3xx itself; this makes the legacy wire safe the same way).
builder.Services.AddHttpClient("a2a")
    .ConfigurePrimaryHttpMessageHandler(() => new SocketsHttpHandler { AllowAutoRedirect = false });
builder.Services.AddSingleton(sp =>
{
    var http = sp.GetRequiredService<IHttpClientFactory>().CreateClient("a2a");
    http.Timeout = TimeSpan.FromSeconds(30);
    return new A2AClient(
        http,
        settings,
        sp.GetRequiredService<AuthServerClient>(),
        sp.GetRequiredService<Microsoft.Extensions.Logging.ILogger<A2AClient>>()
    );
});
// Cross-cutting agent pipeline (issue #12) — resolved by
// Agents.SpecialistPipeline / SpecialistAgentFactory.Create. AgentHost.Build
// registers these for the 5 specialists; the orchestrator builds its own
// WebApplication directly, so it needs the same registrations here.
builder.Services.AddSingleton<AgentRunLogger>();
builder.Services.AddSingleton<ToolAuditMiddleware>();
builder.Services.AddSingleton<PiiRedactor>();
builder.Services.AddSingleton<ContextEnricher>();
builder.Services.AddSingleton<HitlGate>();

// Session history + checkpoint storage. Both factories and their three backends
// each (memory/file/postgres) existed and were unit-tested while nothing in src/
// called them, so MAF_SESSION_BACKEND and MAF_CHECKPOINT_BACKEND read from the
// environment, validated, and then affected nothing (issue #29).
//
// Registering them was necessary and, for checkpoints, not sufficient: the DI
// entry existed for a while afterwards with still no consumer, so
// workflow_checkpoints stayed empty and GET /api/runs/{id}/checkpoints kept
// answering with an empty list. What closes it is that the workflow modes now
// take this CheckpointManager and MAF writes through it during a run (#33).
builder.Services.AddSingleton<ISessionHistoryProvider>(sp =>
    SessionProviderFactory.Build(settings, sp.GetRequiredService<DatabasePool>()));
builder.Services.AddSingleton(sp =>
    CheckpointStorageFactory.Build(
        settings, sp.GetRequiredService<DatabasePool>(), workflowName: "return-replace"));

// Orchestration modes. Two registered: tool and workflow:pre-purchase.
// The rest are honestly absent rather than registered-and-broken — see
// ModeRegistry's remarks.
builder.Services.AddSingleton<IOrchestrationMode>(sp => new ToolRouterMode(sp.GetRequiredService<AIAgent>(), sp.GetRequiredService<AgentSettings>().LlmModel));
builder.Services.AddSingleton<IOrchestrationMode>(sp => new PrePurchaseMode(sp.GetRequiredService<DatabasePool>()));
builder.Services.AddSingleton<IOrchestrationMode>(sp =>
    new ReturnReplaceMode(
        sp.GetRequiredService<DatabasePool>(),
        settings,
        sp.GetRequiredService<CheckpointManager>()));
builder.Services.AddSingleton<IOrchestrationMode>(sp =>
    new HandoffMode(settings, sp.GetRequiredService<A2AClient>(), sp.GetRequiredService<PromptLoader>()));
builder.Services.AddSingleton<IOrchestrationMode>(sp =>
    new GroupChatMode(settings, sp.GetRequiredService<PromptLoader>()));
builder.Services.AddSingleton<ModeRegistry>();
// The approvals inbox: the unified API plus the legacy resume / admin approve-deny routes all decide through one service.
builder.Services.AddApprovals();

builder.Services.AddSingleton<SlidingWindowRateLimiter>();
builder.Services.AddSingleton<OrchestratorTools>();
builder.Services.AddSingleton<AIAgent>(sp =>
{
    var prompts = sp.GetRequiredService<PromptLoader>();
    var tools = sp.GetRequiredService<OrchestratorTools>();
    return OrchestratorAgentFactory.Create(settings, prompts, tools, services: sp);
});

// Mirrors the Python orchestrator's CORSMiddleware: explicit origins from CORS_ALLOWED_ORIGINS
// (default http://localhost:3000), no credentials. The browser normally reaches this API through
// the Next.js same-origin proxy; the list only matters for direct calls (NEXT_PUBLIC_API_URL).
// Auth is a Bearer header, never a cookie, so AllowCredentials is deliberately absent.
var corsOrigins = settings.CorsAllowedOrigins
    .Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
    .Select(o => o.TrimEnd('/'))
    .ToArray();
builder.Services.AddCors(options =>
{
    options.AddDefaultPolicy(policy => policy
        .WithOrigins(corsOrigins)
        .AllowAnyMethod()
        .AllowAnyHeader());
});

var app = builder.Build();
app.Services.GetRequiredService<PromptLoader>().ValidateModelOverrides(); // fail at startup, naming the file
ReturnReplaceMode.CheckPolicyFile(); // log loudly if approvals.yaml is broken; the return gate fails closed regardless

app.UseCors();
app.UseAgentAuth(isOrchestrator: true);

app.MapGet("/", () => Results.Ok(new { status = "ok", service = "orchestrator", port = 8080 }));
app.MapGet("/health", () => Results.Ok(new { healthy = true }));
app.MapReadiness("orchestrator");

app.MapAuthRoutes();
app.MapChatRoutes();
app.MapOrchestrationRoutes();
app.MapConversationRoutes();
app.MapProductRoutes();
app.MapOrderRoutes();
app.MapCartRoutes();
app.MapCheckoutRoutes();
app.MapProfileRoutes();
app.MapReturnLabelRoutes();
app.MapMarketplaceRoutes();
app.MapAdminRoutes();
app.MapSellerRoutes();
app.MapAgentStatsRoutes();
app.MapRunsRoutes();
app.MapHitlRoutes();
app.MapApprovalRoutes();

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
