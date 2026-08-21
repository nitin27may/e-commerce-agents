using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Orchestration;
using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;

namespace ECommerceAgents.Orchestrator.Modes;

/// <summary>
/// The default mode: one LLM turn holding <c>call_specialist_agent</c>, which
/// picks a specialist per request. Mirrors Python's <c>ToolRouterMode</c>.
/// </summary>
/// <remarks>
/// <see cref="GraphMermaid"/> is null on purpose. There is no fixed topology
/// to draw — the model decides where a turn goes, and a diagram claiming
/// otherwise would be a picture of something that doesn't happen.
///
/// Note this mode is not what <c>/api/chat</c> actually executes today: that
/// path still calls the agent directly so it keeps token-level streaming,
/// which routing through <see cref="RunAsync"/> would flatten to a single
/// chunk. This exists so the tool router is describable and comparable
/// alongside the others — <c>GET /modes</c> and <c>POST /compare</c> both
/// need it — rather than being a hole in the registry. Python makes the same
/// split for the same reason.
/// </remarks>
public sealed class ToolRouterMode(AIAgent agent) : IOrchestrationMode
{
    private readonly AIAgent _agent = agent;

    public string Name => "tool";
    public string Label => "Tool Router";
    public string Description =>
        "One LLM turn with call_specialist_agent — the model picks a specialist per request.";

    public ModeCapabilities Capabilities => new(
        Streams: true,
        SupportsHitl: false,
        SupportsCheckpoints: false,
        IsGraph: false
    );

    public string? GraphMermaid() => null;

    public async Task<ModeRunResult> RunAsync(string message, RunContext ctx, CancellationToken ct = default)
    {
        var messages = ctx.History
            .Where(h => h.Role is "user" or "assistant")
            .Select(h => new ChatMessage(h.Role == "assistant" ? ChatRole.Assistant : ChatRole.User, h.Content))
            .ToList();

        if (messages.Count == 0)
        {
            messages.Add(new ChatMessage(ChatRole.User, message));
        }

        var response = await _agent.RunAsync(messages, cancellationToken: ct);

        var steps = RequestContext.CurrentSteps;
        return new ModeRunResult(
            response.Text,
            RequestContext.CurrentInvokedAgents.Count > 0
                ? RequestContext.CurrentInvokedAgents.ToList()
                : ["orchestrator"],
            steps.Count
        );
    }
}
