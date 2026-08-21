using ECommerceAgents.Shared.Agents;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Orchestration;
using ECommerceAgents.Shared.Prompts;
using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;

namespace ECommerceAgents.Orchestrator.Modes;

/// <summary>
/// A round-table: named panelists answer in turn over a shared transcript, then a
/// moderator synthesises a verdict. The .NET twin of Python's
/// <c>orchestrator/modes/group_chat_mode.py</c> (#19).
/// </summary>
/// <remarks>
/// The distinction this mode exists to demonstrate: tool routing calls <b>one</b>
/// specialist, handoff passes control from one to another, and here <b>everyone speaks</b>
/// — each panelist sees what was said before them, so the later takes are responses
/// rather than parallel monologues. That sequencing is the whole point; running the
/// panelists concurrently would produce three unrelated opinions and a moderator with
/// nothing to reconcile.
///
/// Built on a plain sequential loop rather than MAF's <c>GroupChatWorkflowBuilder</c>.
/// The builder's round-robin manager is designed for agents conversing until a
/// termination condition, whereas this is a fixed two-panelists-then-moderator shape
/// whose value is being explainable — matching Python's implementation, which made the
/// same choice for the same reason. The panel prompts are copied from it verbatim so
/// both stacks answer a question the same way.
/// </remarks>
public sealed class GroupChatMode(AgentSettings settings, PromptLoader prompts) : IOrchestrationMode
{
    private readonly AgentSettings _settings = settings;
    private readonly PromptLoader _prompts = prompts;

    /// <summary>Panelist name → its angle. Copied from Python's <c>_PANEL_PROMPTS</c>.</summary>
    private static readonly (string Name, string Instructions)[] Panel =
    [
        ("value",
            "You are the value/pricing panelist on a purchase-decision round-table. "
            + "Given the question and what's been said so far, give a short (2-3 sentence) "
            + "take from a price-and-value angle only. Don't repeat prior speakers."),
        ("quality",
            "You are the quality/reviews panelist on a purchase-decision round-table. "
            + "Given the question and what's been said so far, give a short (2-3 sentence) "
            + "take from a build-quality-and-reviews angle only. Don't repeat prior speakers."),
    ];

    private const string ModeratorInstructions =
        "You are the moderator of a purchase-decision round-table. Read the panelists' takes "
        + "and give the customer a single clear recommendation in 2-3 sentences. Name the "
        + "trade-off the panel disagreed on, if any.";

    public string Name => "group-chat";
    public string Label => "Group Chat (round-table debate)";

    public string Description =>
        "Named panelists take turns over a shared transcript — each sees prior turns before "
        + "speaking — then a moderator synthesizes a verdict. Distinct from tool routing (one "
        + "specialist call) and handoff (control changes hands); every panelist speaks.";

    public ModeCapabilities Capabilities => new(
        Streams: false,
        SupportsHitl: false,
        SupportsCheckpoints: false,
        IsGraph: true);

    /// <remarks>
    /// Node ids use underscores because Mermaid ids cannot contain dashes and the UI
    /// correlates a live <c>node</c> event to a diagram node by id — the convention
    /// <see cref="OrchestrationEvent.ToNodeId"/> exists for.
    /// </remarks>
    public string? GraphMermaid() => """
        graph LR
            panel_value[Value panelist] --> panel_quality[Quality panelist]
            panel_quality --> moderator[Moderator]
        """;

    public async Task<ModeRunResult> RunAsync(string message, RunContext ctx, CancellationToken ct = default)
    {
        var transcript = new List<(string Speaker, string Text)>();
        var spoke = new List<string>();

        foreach (var (name, instructions) in Panel)
        {
            ctx.Events?.Report(OrchestrationEvent.NodeEnter($"panel-{name}"));

            var take = await SpeakAsync(
                $"panel-{name}",
                instructions,
                $"Question: {message}\n\n{FormatTranscript(transcript)}",
                ct);

            transcript.Add((name, take));
            spoke.Add(name);
            ctx.Events?.Report(OrchestrationEvent.NodeExit($"panel-{name}"));
        }

        ctx.Events?.Report(OrchestrationEvent.NodeEnter("moderator"));
        var verdict = await SpeakAsync(
            "moderator",
            ModeratorInstructions,
            $"Question: {message}\n\n{FormatTranscript(transcript)}",
            ct);
        ctx.Events?.Report(OrchestrationEvent.NodeExit("moderator"));

        // The transcript is returned, not just the verdict. A round-table whose
        // deliberation is hidden reads exactly like a single answer, which would make
        // the mode indistinguishable from tool routing to anyone watching the output.
        var text = string.Join("\n\n",
            transcript.Select(t => $"**{Capitalise(t.Speaker)}:** {t.Text}")
                .Append($"**Verdict:** {verdict}"));

        return new ModeRunResult(text, [.. spoke, "moderator"], transcript.Count + 1);
    }

    private async Task<string> SpeakAsync(string name, string instructions, string prompt, CancellationToken ct)
    {
        // A bare chat client, no tools: panelists reason over what is already in the
        // transcript rather than fetching more. Giving them tools would turn each turn
        // into its own research task and lose the debate shape.
        var agent = ChatClientFactory.Create(_settings)
            .AsAIAgent(instructions: instructions, name: name);

        var reply = await agent.RunAsync(prompt, cancellationToken: ct);
        return reply.Text.Trim();
    }

    private static string FormatTranscript(List<(string Speaker, string Text)> transcript) =>
        transcript.Count == 0
            ? "No one has spoken yet. You are first."
            : "So far:\n" + string.Join("\n", transcript.Select(t => $"- {t.Speaker}: {t.Text}"));

    private static string Capitalise(string s) =>
        string.IsNullOrEmpty(s) ? s : char.ToUpperInvariant(s[0]) + s[1..];
}
