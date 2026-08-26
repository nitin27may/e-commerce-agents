// MAF v1 — Chapter 22: Group-Chat Debate / Round-Table Orchestration (.NET)
//
// Named panelists answer in turn over a shared transcript, then a moderator
// synthesises a verdict. The sixth orchestration pattern, added after the
// capstone.
//
// The distinction the pattern exists to demonstrate: tool routing calls ONE
// specialist, handoff (chapter 14) passes control from one to another, and
// here EVERYONE speaks — each panelist sees what was said before them, so the
// later takes are responses rather than parallel monologues. That sequencing
// is the whole point. Run the panelists concurrently (chapter 13) and you get
// three unrelated opinions and a moderator with nothing to reconcile.
//
// Deterministic by default: panelists are plain callables, no LLM. The pattern
// is about sequencing and shared state, and a live model would only make it
// harder to see.
//
// ── One asymmetry with the Python chapter, stated rather than hidden ────────
//
// Python's version imports the PRODUCTION module — agents/python/workflows/
// group_chat.py — and demonstrates the real class. There is no equivalent
// standalone type on the .NET side: production's round-table lives inside
// ECommerceAgents.Orchestrator.Modes.GroupChatMode as an LLM-backed sequential
// loop, not a reusable workflow class.
//
// So this file reimplements the same SHAPE standalone rather than referencing
// the orchestrator, which keeps the chapter self-contained like every other
// one in the series. The behaviour matches Python's demo exactly; what differs
// is that the Python chapter is a tour of shipped production code and this one
// is a faithful model of it. Worth knowing before treating the two as
// interchangeable.
//
// Run:
//   cd tutorials/22-group-chat-debate/dotnet
//   dotnet run

using Microsoft.Agents.AI.Workflows;

namespace MafV1.Ch22.GroupChatDebate;

/// <summary>One panelist's contribution to the transcript.</summary>
public sealed record Turn(string Speaker, string Text);

/// <summary>
/// Shared state threaded through the round-table.
/// </summary>
/// <remarks>
/// Mutable and passed by reference from executor to executor — that IS the
/// shared transcript. A record with value semantics here would give each
/// panelist a private copy and quietly turn the round-table into a fan-out.
/// </remarks>
public sealed class GroupChatState(string question)
{
    public string Question { get; } = question;

    public List<Turn> Transcript { get; } = new();

    public string Verdict { get; set; } = string.Empty;

    public List<string> CompletedSteps { get; } = new();
}

/// <summary>
/// Given the question and the transcript so far, return this panelist's
/// contribution.
/// </summary>
/// <remarks>
/// Async so a real agent-backed panelist fits without changing the signature —
/// the production .NET round-table (Orchestrator.Modes.GroupChatMode) awaits a
/// model here.
/// </remarks>
public delegate ValueTask<string> Responder(string question, IReadOnlyList<Turn> transcript);

/// <summary>One panelist's turn: append a contribution to the shared transcript.</summary>
[SendsMessage(typeof(GroupChatState))]
internal sealed partial class PanelistExecutor(string name, Responder responder)
    : Executor($"panelist-{name}")
{
    [MessageHandler]
    public async ValueTask HandleAsync(
        GroupChatState state,
        IWorkflowContext context,
        CancellationToken cancellationToken = default)
    {
        string text;
        try
        {
            text = await responder(state.Question, state.Transcript).ConfigureAwait(false);
        }
        catch (Exception ex)
        {
            // A panelist that throws must not take the panel down. The failure
            // becomes a visible turn instead — the moderator can then reconcile
            // around a missing voice, which is far better than the whole
            // round-table failing because one specialist timed out.
            text = $"({name} could not respond: {ex.Message})";
        }

        state.Transcript.Add(new Turn(name, text));
        state.CompletedSteps.Add(name);

        await context.SendMessageAsync(state, cancellationToken: cancellationToken).ConfigureAwait(false);
    }
}

/// <summary>Final turn: synthesize the transcript into a verdict.</summary>
[YieldsOutput(typeof(GroupChatState))]
internal sealed partial class ModeratorExecutor(Func<GroupChatState, string>? synthesizer)
    : Executor("moderator")
{
    [MessageHandler]
    public async ValueTask HandleAsync(
        GroupChatState state,
        IWorkflowContext context,
        CancellationToken cancellationToken = default)
    {
        state.Verdict = (synthesizer ?? GroupChatWorkflow.DefaultSynthesis)(state);
        state.CompletedSteps.Add("moderator");

        await context.YieldOutputAsync(state, cancellationToken).ConfigureAwait(false);
    }
}

/// <summary>A sequential round-table of panelists followed by a moderator.</summary>
/// <param name="Panelists">
/// Ordered (name, responder) pairs. Each runs once, in order, seeing the
/// transcript accumulated by earlier panelists.
/// </param>
/// <param name="Synthesizer">Optional verdict builder over the final state.</param>
public sealed record GroupChatWorkflow(
    IReadOnlyList<(string Name, Responder Responder)> Panelists,
    Func<GroupChatState, string>? Synthesizer = null)
{
    public static string DefaultSynthesis(GroupChatState state) =>
        $"Synthesized {state.Transcript.Count} perspective(s) "
        + $"({string.Join(", ", state.Transcript.Select(t => t.Speaker))}) on: {state.Question}";

    internal Workflow Build()
    {
        if (Panelists.Count == 0)
        {
            // A round-table with no panelists is a moderator summarizing
            // silence. Failing loudly beats emitting a confident verdict about
            // nothing.
            throw new ArgumentException("group chat needs at least one panelist", nameof(Panelists));
        }

        var executors = Panelists.Select(p => new PanelistExecutor(p.Name, p.Responder)).ToList();
        var moderator = new ModeratorExecutor(Synthesizer);

        WorkflowBuilder builder = new(executors[0]);
        for (int i = 0; i < executors.Count - 1; i++)
        {
            builder = builder.AddEdge(executors[i], executors[i + 1]);
        }

        return builder
            .AddEdge(executors[^1], moderator)
            .WithOutputFrom(moderator)
            .Build();
    }

    /// <summary>Runs the round-table and returns the final populated state.</summary>
    public async Task<GroupChatState> ExecuteAsync(string question)
    {
        Workflow workflow = Build();
        var state = new GroupChatState(question);

        await using StreamingRun run = await InProcessExecution.RunStreamingAsync(workflow, state);
        await foreach (WorkflowEvent evt in run.WatchStreamAsync())
        {
            if (evt is WorkflowOutputEvent { Data: GroupChatState final })
            {
                state = final;
            }
        }

        return state;
    }
}

public static class Program
{
    public static ValueTask<string> ValueVoice(string question, IReadOnlyList<Turn> transcript) =>
        ValueTask.FromResult("Strong price for the feature set; frequent discounts.");

    public static ValueTask<string> QualityVoice(string question, IReadOnlyList<Turn> transcript) =>
        // Reads the transcript, which is what makes this a debate rather than a
        // survey. A panelist that ignores prior turns would produce the same
        // answer under concurrent orchestration.
        ValueTask.FromResult(
            $"Considering {transcript.Count} prior point(s): reviews show excellent build quality.");

    public static string Synthesize(GroupChatState state) =>
        $"Verdict on '{state.Question}': both value and quality perspectives are "
        + $"positive across {state.Transcript.Count} turns — recommended.";

    public static GroupChatWorkflow BuildWorkflow() => new(
        new[]
        {
            ("value", new Responder(ValueVoice)),
            ("quality", new Responder(QualityVoice)),
        },
        Synthesize);

    public static async Task<int> Main()
    {
        GroupChatState state = await BuildWorkflow()
            .ExecuteAsync("Is the Sony WH-1000XM5 worth it?");

        Console.WriteLine("Transcript:");
        foreach (Turn turn in state.Transcript)
        {
            Console.WriteLine($"  {turn.Speaker,8}: {turn.Text}");
        }

        Console.WriteLine();
        Console.WriteLine("Moderator verdict:");
        Console.WriteLine($"  {state.Verdict}");

        return 0;
    }
}
