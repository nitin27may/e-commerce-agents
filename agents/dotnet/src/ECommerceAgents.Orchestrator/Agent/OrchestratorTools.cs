using ECommerceAgents.Shared.A2A;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using Microsoft.Extensions.AI;
using Microsoft.Extensions.Logging;
using System.ComponentModel;

namespace ECommerceAgents.Orchestrator.Agent;

/// <summary>
/// Single tool the orchestrator agent uses to route a request to a
/// specialist. Mirrors Python's <c>call_specialist_agent</c> — the
/// agent's LLM picks the target by name, we translate the name into a
/// base URL via <see cref="AgentSettings.AgentRegistry"/> and POST the
/// message over A2A HTTP.
/// </summary>
public sealed class OrchestratorTools(A2AClient client, AgentSettings settings, ILogger<OrchestratorTools> logger)
{
    private readonly A2AClient _client = client;
    private readonly IReadOnlyDictionary<string, string> _registry = AgentSettingsLoader.ParseAgentRegistry(settings);
    private readonly ILogger<OrchestratorTools> _logger = logger;

    public IEnumerable<AITool> All() => new AITool[]
    {
        AIFunctionFactory.Create(CallSpecialistAgent, nameof(CallSpecialistAgent)),
    };

    [Description("Route a request to a specialist agent via A2A. Available agents: product-discovery, order-management, pricing-promotions, review-sentiment, inventory-fulfillment")]
    public async Task<string> CallSpecialistAgent(
        [Description("Name of the specialist agent to call")] string agentName,
        [Description("The message to send to the specialist agent")] string message
    )
    {
        if (!_registry.TryGetValue(agentName, out var url))
        {
            var available = string.Join(", ", _registry.Keys);
            _logger.LogWarning("a2a.unknown_target name={Agent}", agentName);
            return $"Unknown agent: {agentName}. Available agents: {available}";
        }

        return await _client.SendAsync(agentName, url, message, RequestContext.CurrentHistory);
    }
}
