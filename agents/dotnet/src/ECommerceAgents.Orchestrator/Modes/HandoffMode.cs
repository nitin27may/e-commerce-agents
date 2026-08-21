using ECommerceAgents.Shared.A2A;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Orchestration;
using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;
using System.Text.Json;

namespace ECommerceAgents.Orchestrator.Modes;

/// <summary>
/// Control is handed to a specialist and handed back, rather than the model deciding
/// per turn via a tool call. The .NET twin of Python's
/// <c>orchestrator/modes/handoff_mode.py</c> (#19).
/// </summary>
/// <remarks>
/// The contrast this mode exists to show sits against <see cref="ToolRouterMode"/>. Tool
/// routing keeps the orchestrator in charge for the whole turn: it calls
/// <c>call_specialist_agent</c>, gets a string back, and composes the final answer
/// itself. Handoff transfers the turn — the specialist's answer <b>is</b> the answer, and
/// the orchestrator's job ends at choosing who takes it. A reader comparing the two in
/// the mode switcher should see the same question answered in a recognisably different
/// voice, which is the teaching point.
///
/// The transfer target is chosen by a single routing call with no tools attached, rather
/// than by the orchestrator's full tool-calling agent. Reusing that agent would let it
/// answer the question itself, and then nothing would ever be handed off — the mode would
/// silently degrade into tool routing while still being labelled handoff.
///
/// MAF .NET ships <c>HandoffWorkflowBuilder</c>, which is the natural fit once specialists
/// are in-process <c>AIAgent</c>s. Here they are separate services reached over A2A, so a
/// remote-agent adapter would have to exist first — Python has one
/// (<c>shared/remote_agent.py::RemoteSpecialistChatClient</c>) and .NET does not. This
/// implements the same observable behaviour over the existing A2A client and is honest
/// about the difference rather than claiming a builder it does not use; the adapter is
/// worth building when a second mode needs it.
/// </remarks>
public sealed class HandoffMode(AgentSettings settings, A2AClient a2a) : IOrchestrationMode
{
    private readonly AgentSettings _settings = settings;
    private readonly A2AClient _a2a = a2a;

    public string Name => "handoff";
    public string Label => "Handoff Mesh";

    public string Description =>
        "The orchestrator hands control to a specialist and back, instead of deciding "
        + "per-turn via a tool call. The specialist's answer is the answer.";

    public ModeCapabilities Capabilities => new(
        Streams: false,
        // Neither the approval middleware nor an in-workflow gate is wired into this
        // path, and saying so is better than implying a gate that is not there.
        SupportsHitl: false,
        SupportsCheckpoints: false,
        IsGraph: true);

    public string? GraphMermaid()
    {
        var nodes = Registry().Keys
            .Select(name => $"    orchestrator --> {OrchestrationEvent.ToNodeId(name)}[{name}]");
        return "graph LR\n    orchestrator[Orchestrator]\n" + string.Join("\n", nodes);
    }

    public async Task<ModeRunResult> RunAsync(string message, RunContext ctx, CancellationToken ct = default)
    {
        var registry = Registry();
        if (registry.Count == 0)
        {
            // A mesh with no members cannot hand anything off. Saying that beats an
            // apology that reads like the specialist had nothing useful to add.
            return new ModeRunResult(
                "No specialists are registered, so there is nobody to hand this to.",
                ["orchestrator"],
                0);
        }

        ctx.Events?.Report(OrchestrationEvent.NodeEnter("orchestrator"));
        var target = await ChooseTargetAsync(message, registry.Keys.ToList(), ct);
        ctx.Events?.Report(OrchestrationEvent.NodeExit("orchestrator"));

        ctx.Events?.Report(OrchestrationEvent.NodeEnter(target));
        var answer = await _a2a.SendAsync(target, registry[target], message, ctx.History, ct);
        ctx.Events?.Report(OrchestrationEvent.NodeExit(target));

        // Returned as the specialist wrote it. Re-composing it here would put the
        // orchestrator back in charge of the turn and erase the only difference between
        // this mode and tool routing.
        return new ModeRunResult(answer, ["orchestrator", target], 1);
    }

    /// <summary>
    /// Picks who takes the turn. One call, no tools, name-only answer.
    /// </summary>
    private async Task<string> ChooseTargetAsync(string message, List<string> names, CancellationToken ct)
    {
        var agent = ECommerceAgents.Shared.Agents.ChatClientFactory.Create(_settings).AsAIAgent(
            instructions:
                "You route a customer message to exactly one specialist. Reply with the "
                + "specialist's name and nothing else. Available specialists:\n"
                + string.Join("\n", names.Select(n => $"- {n}")),
            name: "handoff-router");

        try
        {
            var reply = await agent.RunAsync(message, cancellationToken: ct);
            var choice = reply.Text.Trim().Trim('.', '"', '\'');

            // Substring match both ways: models answer "order-management" but also
            // "the order-management specialist", and refusing the second would fall back
            // for a correct answer that was merely wordier than asked.
            var matched = names.FirstOrDefault(n =>
                choice.Contains(n, StringComparison.OrdinalIgnoreCase)
                || n.Contains(choice, StringComparison.OrdinalIgnoreCase));

            return matched ?? names[0];
        }
        catch (Exception)
        {
            // A routing failure should not lose the customer's question. Handing it to
            // the first specialist gets a real answer; failing the turn gets none.
            return names[0];
        }
    }

    private Dictionary<string, string> Registry()
    {
        try
        {
            var parsed = JsonSerializer.Deserialize<Dictionary<string, string>>(_settings.AgentRegistry);
            return parsed?.Where(kv => !string.IsNullOrWhiteSpace(kv.Value))
                .ToDictionary(kv => kv.Key, kv => kv.Value) ?? [];
        }
        catch (JsonException)
        {
            return [];
        }
    }
}
