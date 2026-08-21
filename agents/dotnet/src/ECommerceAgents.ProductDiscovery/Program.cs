using ECommerceAgents.ProductDiscovery.Tools;
using ECommerceAgents.Shared.Tools;
using ECommerceAgents.Shared.A2A;
using ECommerceAgents.Shared.Agents;
using ECommerceAgents.Shared.Prompts;
using Microsoft.Agents.AI;
using Microsoft.Extensions.DependencyInjection;

var app = AgentHost.Build(
    name: "product-discovery",
    description: "Finds products via catalog search, filtering, comparison, and trending.",
    port: 8081,
    onMessage: (message, services) => AgentHost.RunAgentWithHistoryAsync(services, message),
    configureServices: (builder, settings) =>
    {
        builder.Services.AddSingleton(new PromptLoader(PromptsRoot()));
        builder.Services.AddSingleton(EmbeddingClientFactory.CreateEmbeddingClient(settings));
        builder.Services.AddSingleton<ProductTools>();
        builder.Services.AddSingleton<UserProfileTools>();
        builder.Services.AddSingleton<StockLookupTools>();
        builder.Services.AddSingleton<PriceHistoryTools>();
        builder.Services.AddSingleton<MemoryTools>();
        builder.Services.AddSingleton<AIAgent>(sp =>
        {
            var prompts = sp.GetRequiredService<PromptLoader>();
            var tools = sp.GetRequiredService<ProductTools>();
            var userProfileTools = sp.GetRequiredService<UserProfileTools>();
            var stockLookupTools = sp.GetRequiredService<StockLookupTools>();
            var priceHistoryTools = sp.GetRequiredService<PriceHistoryTools>();
            var memoryTools = sp.GetRequiredService<MemoryTools>();
            return SpecialistAgentFactory.Create(settings, prompts, "product-discovery", tools.All().Concat(userProfileTools.All()).Concat(stockLookupTools.All()).Concat(priceHistoryTools.All()).Concat(memoryTools.All()), services: sp);
        });
    }
);

app.Run(Environment.GetEnvironmentVariable("ASPNETCORE_URLS") ?? "http://0.0.0.0:8081");


// Locate the shared prompts root — the YAML files live in
// agents/config/prompts/ and are shared with the Python backend, so we
// walk up from the binary to find the repo root. Container builds should
// override this by placing the prompts directory beside the binary.
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
