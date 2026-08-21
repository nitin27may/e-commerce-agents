using ECommerceAgents.InventoryFulfillment.Tools;
using ECommerceAgents.Shared.Tools;
using ECommerceAgents.Shared.A2A;
using ECommerceAgents.Shared.Agents;
using ECommerceAgents.Shared.Prompts;
using Microsoft.Agents.AI;
using Microsoft.Extensions.DependencyInjection;

var app = AgentHost.Build(
    name: "inventory-fulfillment",
    description: "Answers stock, warehouse, shipping and fulfillment questions.",
    port: 8085,
    onMessage: (message, services) => AgentHost.RunAgentWithHistoryAsync(services, message),
    configureServices: (builder, settings) =>
    {
        builder.Services.AddSingleton(new PromptLoader(PromptsRoot()));
        builder.Services.AddSingleton<InventoryTools>();
        builder.Services.AddSingleton<UserProfileTools>();
        builder.Services.AddSingleton<StockLookupTools>();
        builder.Services.AddSingleton<ProductLookupTools>();
        builder.Services.AddSingleton<AIAgent>(sp =>
        {
            var prompts = sp.GetRequiredService<PromptLoader>();
            var tools = sp.GetRequiredService<InventoryTools>();
            var userProfileTools = sp.GetRequiredService<UserProfileTools>();
            var stockLookupTools = sp.GetRequiredService<StockLookupTools>();
            // Shared lookup so a product *name* can reach the id-keyed tools below
            // (issue #18) — matches which agents Python attaches it to.
            var lookup = sp.GetRequiredService<ProductLookupTools>();
            return SpecialistAgentFactory.Create(settings, prompts, "inventory-fulfillment", tools.All().Concat(lookup.All()).Concat(userProfileTools.All()).Concat(stockLookupTools.All()), services: sp);
        });
    }
);

app.Run(Environment.GetEnvironmentVariable("ASPNETCORE_URLS") ?? "http://0.0.0.0:8085");


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
