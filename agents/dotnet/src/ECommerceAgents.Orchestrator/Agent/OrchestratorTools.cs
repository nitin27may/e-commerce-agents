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

        // Backs the streaming chat endpoint's dynamic agents_involved (mirrors
        // Python's current_steps capture) — a no-op for callers that never set up
        // a RequestContext.Scope, so this is safe outside a chat request too.
        RequestContext.RecordInvokedAgent(agentName);

        // A stream writer is only present inside ChatRoutes.StreamAsync (issue
        // #14) — the .NET analog of Python's call_specialist_agent forwarding
        // into current_stream_queue. Outside a streaming turn (blocking
        // /api/chat, or this tool exercised directly in a test) this is null and
        // we fall back to the plain blocking call, same as before this change.
        var streamWriter = RequestContext.CurrentStreamWriter;
        if (streamWriter is null)
        {
            return await _client.SendAsync(agentName, url, message, RequestContext.CurrentHistory);
        }

        var full = new System.Text.StringBuilder();
        await foreach (var delta in _client.StreamAsync(agentName, url, message, RequestContext.CurrentHistory))
        {
            full.Append(delta);
            await streamWriter.WriteAsync(delta);
        }
        return full.ToString();
    }
}
