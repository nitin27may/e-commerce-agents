using ECommerceAgents.Shared.A2A;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Orchestration;
using Microsoft.Agents.AI;
using ECommerceAgents.Shared.Prompts;
using Microsoft.Agents.AI.Workflows;
using Microsoft.Extensions.AI;

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
/// itself. Handoff <b>transfers ownership</b> — the specialist's answer is the answer, and
/// the triage agent's job ends at choosing who takes it.
///
/// This is a real <c>AgentWorkflowBuilder.CreateHandoffBuilderWith</c> mesh. It used to be
/// a hand-rolled router — one routing call, one A2A call, return the reply — which
/// produced the right-looking output while being a different pattern entirely. By
/// Microsoft's own taxonomy that was agent-as-tool wearing handoff's name: control
/// returned to the caller rather than transferring, so the two modes differed in label
/// more than in behaviour.
///
/// What unblocked the real thing is <see cref="RemoteSpecialistChatClient"/>. MAF's
/// handoff orchestration takes <see cref="AIAgent"/> participants, and this repo's
/// specialists are separate services behind A2A; without an adapter there was no way to
/// put them in a mesh at all. The previous implementation said so explicitly and deferred
/// the adapter until a second mode needed it.
///
/// <para><b>The triage agent carries no tools, and that is load-bearing.</b></para>
///
/// Handoffs happen through tool calls, so an agent that answers instead of calling a
/// handoff tool leaves the workflow with nowhere to go. Microsoft's guidance is explicit:
/// "if an agent does not call a handoff tool but generates a response instead, the
/// workflow won't know what to do next but to delegate back to the user for further
/// input." With autonomous mode on, that becomes a self-continuation loop.
///
/// Python learned this the expensive way. Its handoff mode used the tool-router
/// orchestrator as the start agent — carrying <c>call_specialist_agent</c> and a prompt
/// naming it — so it never handed off. Measured: 5,403 streamed updates, 23,637
/// characters, 100-200 seconds, no specialist reached. Both stacks now use the same
/// tool-free triage agent, loaded from the same shared prompt corpus
/// (<c>config/prompts/handoff-triage.yaml</c>).
/// </remarks>
public sealed class HandoffMode(AgentSettings settings, A2AClient a2a, PromptLoader prompts) : IOrchestrationMode
{
    private readonly AgentSettings _settings = settings;
    private readonly A2AClient _a2a = a2a;
    private readonly PromptLoader _prompts = prompts;

    public string Name => "handoff";
    public string Label => "Handoff Mesh";

    public string Description =>
        "The orchestrator hands control to a specialist and back, instead of deciding "
        + "per-turn via a tool call. The specialist's answer is the answer.";

    public ModeCapabilities Capabilities => new(
        Streams: true,
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

        AIAgent triage = BuildTriageAgent();
        List<AIAgent> specialists = registry
            .Select(kv => BuildRemoteSpecialist(kv.Key, kv.Value))
            .ToList();

        Workflow workflow = AgentWorkflowBuilder.CreateHandoffBuilderWith(triage)
            .WithHandoffs(triage, specialists)
            // Specialists hand back to triage rather than to each other. That path
            // already exists via a triage round-trip, and a full cross-mesh makes the
            // routing graph much harder to reason about from a support-ops view.
            .WithHandoffs(specialists, triage, "Return to triage for further routing.")
            // Bounded. Autonomous mode's contract is "if the agent does not hand off,
            // feed it a continuation prompt and run it again" — so the ceiling is the
            // only thing standing between a non-handing-off agent and a long monologue.
            // MAF's default is 50.
            .WithAutonomousMode(turnLimit: _settings.HandoffMaxTurns, agents: [triage])
            .Build();

        var messages = new List<ChatMessage> { new(ChatRole.User, message) };

        await using StreamingRun run = await InProcessExecution
            .RunStreamingAsync(workflow, messages, cancellationToken: ct);

        // Wrapped agents are lazy: without a TurnToken they cache their input and never
        // call anything. A run missing this completes normally having done nothing.
        await run.TrySendMessageAsync(new TurnToken(emitEvents: true));

        var spoke = new List<string>();
        List<ChatMessage>? conversation = null;

        await foreach (WorkflowEvent evt in run.WatchStreamAsync().WithCancellation(ct))
        {
            switch (evt)
            {
                case AgentResponseUpdateEvent update when update.Update is { } chunk:
                    if (!string.IsNullOrEmpty(chunk.AuthorName) && !spoke.Contains(chunk.AuthorName))
                    {
                        spoke.Add(chunk.AuthorName);
                        ctx.Events?.Report(OrchestrationEvent.NodeEnter(chunk.AuthorName));
                    }

                    break;

                case WorkflowOutputEvent output when output.Data is List<ChatMessage> final:
                    conversation = final;
                    break;
            }
        }

        // The specialist's answer IS the answer — recomposing it here would put the
        // triage agent back in charge of the turn and erase the difference between this
        // mode and tool routing.
        string answer = conversation?
            .LastOrDefault(m => m.Role == ChatRole.Assistant && !string.IsNullOrWhiteSpace(m.Text))?
            .Text.Trim() ?? string.Empty;

        // Who actually spoke, in order. Triage usually will not appear: a clean handoff
        // is a tool call with no text, which is the correct outcome rather than a gap.
        List<string> involved = conversation?
            .Where(m => m.Role == ChatRole.Assistant && !string.IsNullOrWhiteSpace(m.AuthorName))
            .Select(m => m.AuthorName!)
            .Distinct()
            .ToList() ?? [];

        if (involved.Count == 0)
        {
            involved = spoke.Count > 0 ? spoke : ["orchestrator"];
        }

        return new ModeRunResult(answer, involved, involved.Count);
    }

    /// <summary>
    /// The start agent: no tools, no context provider, one instruction — hand off.
    /// </summary>
    /// <remarks>
    /// Uses <c>handoff-triage.yaml</c> from the shared corpus, deliberately NOT
    /// <c>orchestrator.yaml</c>. That file names <c>call_specialist_agent</c>, which is
    /// the tool router's mechanism; telling this agent to use it would point it at a tool
    /// it does not have and it would answer directly instead of handing off.
    /// </remarks>
    private AIAgent BuildTriageAgent() =>
        ECommerceAgents.Shared.Agents.ChatClientFactory.Create(_settings).AsAIAgent(
            instructions: _prompts.Load("handoff-triage"),
            name: "orchestrator",
            description: "Triage agent that routes the conversation to a specialist.");

    /// <summary>
    /// Wraps a remote specialist so the mesh can treat it as an ordinary participant.
    /// </summary>
    private AIAgent BuildRemoteSpecialist(string name, string url) =>
        new RemoteSpecialistChatClient(name, url, _a2a).AsAIAgent(
            // Minimal by design — the specialist on the far side of the A2A hop enforces
            // its own system prompt, tools and grounding. Anything set here would be a
            // second opinion competing with the real agent's.
            instructions: $"You are the remote {name} specialist. Reply directly with the user's request.",
            name: name,
            description: $"Remote specialist {name} reached over A2A.");

    // Deliberately not its own parse. This used to deserialize the registry
    // itself and swallow a JsonException into an empty mesh, which builds and
    // answers — the triage agent has nowhere to hand off to, so it replies
    // itself — turning a config typo into "the model stopped routing".
    // AgentSettingsLoader.ParseAgentRegistry is the one validator, shared with
    // OrchestratorTools and mirrored on the Python stack, and it throws.
    private Dictionary<string, string> Registry() =>
        AgentSettingsLoader.ParseAgentRegistry(_settings).ToDictionary(kv => kv.Key, kv => kv.Value);
}
