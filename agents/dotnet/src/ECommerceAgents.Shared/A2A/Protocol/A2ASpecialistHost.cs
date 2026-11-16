using A2A;
using A2A.AspNetCore;
using ECommerceAgents.Shared.Configuration;
using Microsoft.Agents.AI;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Routing;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;

namespace ECommerceAgents.Shared.A2A.Protocol;

/// <summary>
/// Wires a specialist to speak A2A 1.0 (JSON-RPC binding) at <see cref="A2AWire.RpcPath"/>, with its card at
/// <c>/.well-known/agent-card.json</c>, beside the legacy <c>/message:send</c> and <c>/message:stream</c>
/// routes, which are untouched. Twin of Python's <c>mount_a2a</c>.
/// </summary>
public static class A2ASpecialistHost
{
    private sealed record CardSettings(string AgentName, string Description, int Port, string PublicBaseUrl);

    /// <summary>
    /// Registers the executor, the bounded owner-scoped task store, the active-task limiter, the extension
    /// registry and the SDK server. Call before the host is built; the <see cref="AIAgent"/> is resolved
    /// lazily, so registration order against the agent factory does not matter.
    /// </summary>
    public static IServiceCollection AddA2ASpecialist(
        this IServiceCollection services,
        string agentName,
        string description,
        int port,
        AgentSettings settings)
    {
        var builder = new A2ACustomOperationRegistryBuilder();
        foreach (var uri in A2AWire.AllExtensionUris)
        {
            builder.RegisterExtension(new AgentExtension
            {
                Uri = uri,
                Description = A2AWire.DescriptionOf(uri),
                Required = false,
            });
        }

        var registry = builder.Build();

        services.AddSingleton(registry);
        services.AddSingleton(new CardSettings(agentName, description, port, settings.A2aPublicBaseUrl));
        services.AddSingleton(new ActiveTaskLimiter(settings.A2aMaxActiveTasks, settings.A2aMaxActiveTasksPerCaller));
        services.AddSingleton<OwnedBoundedTaskStore>();
        services.AddSingleton<ChannelEventNotifier>();
        services.AddSingleton<IAgentHandler>(sp => new SpecialistA2AExecutor(
            sp,
            agentName,
            sp.GetRequiredService<ActiveTaskLimiter>(),
            sp.GetRequiredService<OwnedBoundedTaskStore>(),
            sp.GetRequiredService<ILogger<SpecialistA2AExecutor>>()));
        services.AddSingleton<IA2ARequestHandler>(sp => new SpecialistA2AServer(
            sp.GetRequiredService<IAgentHandler>(),
            sp.GetRequiredService<OwnedBoundedTaskStore>(),
            sp.GetRequiredService<ChannelEventNotifier>(),
            sp.GetRequiredService<ILogger<A2AServer>>(),
            new A2AServerOptions { SupportsStreaming = true, SupportsExtendedAgentCard = false },
            sp.GetRequiredService<ActiveTaskLimiter>()));
        return services;
    }

    /// <summary>
    /// Maps the JSON-RPC endpoint and the agent card. The card is rebuilt per request so its interface URL is
    /// the one the caller reached the specialist by (a compose service name, <c>localhost:8081</c> in
    /// development) unless <c>A2A_PUBLIC_BASE_URL</c> pins it. It is public, like <c>/health</c>.
    /// </summary>
    public static IEndpointRouteBuilder MapA2ASpecialist(this WebApplication app)
    {
        var registry = app.Services.GetRequiredService<A2ACustomOperationRegistry>();
        var handler = app.Services.GetRequiredService<IA2ARequestHandler>();
        var card = app.Services.GetRequiredService<CardSettings>();

        app.MapA2A(handler, A2AWire.RpcPath, new A2AExtensionOptions(registry));

        app.MapGet("/.well-known/agent-card.json", (HttpRequest request) =>
        {
            var baseUrl = !string.IsNullOrEmpty(card.PublicBaseUrl)
                ? card.PublicBaseUrl
                : $"{request.Scheme}://{request.Host}";
            var built = SpecialistAgentCard.Build(
                app.Services.GetService<AIAgent>(), card.AgentName, card.Description, baseUrl, registry);
            return Results.Content(SpecialistAgentCard.ToJson(built), "application/json");
        });
        return app;
    }
}
