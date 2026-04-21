using ECommerceAgents.ReviewSentiment.Tools;
using ECommerceAgents.Shared.A2A;
using ECommerceAgents.Shared.Agents;
using ECommerceAgents.Shared.Prompts;
using Microsoft.Agents.AI;
using Microsoft.Extensions.DependencyInjection;

var app = AgentHost.Build(
    name: "review-sentiment",
    description: "Summarises reviews and answers sentiment / quality questions.",
    port: 8084,
    onMessage: async (message, services) =>
    {
        var agent = services.GetRequiredService<AIAgent>();
        var response = await agent.RunAsync(message);
        return response.Text;
    },
    configureServices: (builder, settings) =>
    {
        builder.Services.AddSingleton(new PromptLoader(PromptsRoot()));
        builder.Services.AddSingleton<ReviewTools>();
        builder.Services.AddSingleton<AIAgent>(sp =>
        {
            var prompts = sp.GetRequiredService<PromptLoader>();
            var tools = sp.GetRequiredService<ReviewTools>();
            return SpecialistAgentFactory.Create(settings, prompts, "review_sentiment", tools.All());
        });
    }
);

app.Run(Environment.GetEnvironmentVariable("ASPNETCORE_URLS") ?? "http://0.0.0.0:8084");


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
