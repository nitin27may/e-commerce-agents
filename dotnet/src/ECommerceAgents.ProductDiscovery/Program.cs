using ECommerceAgents.ProductDiscovery.Tools;
using ECommerceAgents.Shared.A2A;
using ECommerceAgents.Shared.Agents;
using ECommerceAgents.Shared.Prompts;
using Microsoft.Agents.AI;
using Microsoft.Extensions.DependencyInjection;

var app = AgentHost.Build(
    name: "product-discovery",
    description: "Finds products via catalog search, filtering, comparison, and trending.",
    port: 8081,
    onMessage: async (message, services) =>
    {
        var agent = services.GetRequiredService<AIAgent>();
        var response = await agent.RunAsync(message);
        return response.Text;
    },
    configureServices: (builder, settings) =>
    {
        builder.Services.AddSingleton(new PromptLoader(PromptsRoot()));
        builder.Services.AddSingleton<ProductTools>();
        builder.Services.AddSingleton<AIAgent>(sp =>
        {
            var prompts = sp.GetRequiredService<PromptLoader>();
            var tools = sp.GetRequiredService<ProductTools>();
            return SpecialistAgentFactory.Create(settings, prompts, "product_discovery", tools.All());
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
    while (dir is not null && !Directory.Exists(Path.Combine(dir.FullName, "agents", "config", "prompts")))
    {
        dir = dir.Parent;
    }
    return dir is not null
        ? Path.Combine(dir.FullName, "agents", "config", "prompts")
        : Path.Combine(AppContext.BaseDirectory, "config", "prompts");
}
