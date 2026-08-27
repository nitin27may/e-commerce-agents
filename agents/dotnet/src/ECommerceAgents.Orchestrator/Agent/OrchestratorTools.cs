using ECommerceAgents.Shared.Tools;
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
        AgentTool.Create(CallSpecialistAgent, nameof(CallSpecialistAgent)),
    };

    /// <remarks>
    /// The parameter is <c>agent_name</c>, not <c>agentName</c>, and that is
    /// deliberate despite being un-idiomatic C#. Parameter names are part of the
    /// tool's JSON schema, so they are a wire contract with the model — and here
    /// also with the shared prompt corpus, which is Python's and names this
    /// parameter <c>agent_name</c>.
    ///
    /// With <c>agentName</c>, the model (primed by a snake_case corpus) emitted
    /// <c>agent_name</c>, the binder rejected it, and <b>every routed request in
    /// the .NET stack failed</b> with "The arguments dictionary is missing a
    /// value for the required parameter 'agentName'". MAF offers no way to rename
    /// a parameter in the schema — <c>[JsonPropertyName]</c> on a parameter is
    /// ignored by <c>AIFunctionFactory</c> — so the C# name has to be the wire
    /// name. See plan 16 F1.
    /// </remarks>
    [Description("Route a request to a specialist agent via A2A. Available agents: product-discovery, order-management, pricing-promotions, review-sentiment, inventory-fulfillment")]
    public async Task<string> CallSpecialistAgent(
        [Description("Name of the specialist agent to call")] string agent_name,
        [Description("The message to send to the specialist agent")] string message
    )
    {
        var agentName = agent_name;

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
            await streamWriter.WriteAsync(new StreamFrame("delta", delta));
        }
        return full.ToString();
    }
}
