// Shared test double for the .NET tutorial chapters.
//
// The .NET counterpart to tutorials/_shared/replay_client.py, and it exists
// for the same reason: every chapter needs to assert "the framework wired
// this up the way the prose says it did" without a key, a network call, or a
// recorded fixture. It is linked into each chapter's test project rather than
// copied, so a fix here reaches every chapter at once:
//
//   <ItemGroup>
//     <Compile Include="../../_shared/dotnet/ScriptedChatClient.cs"
//              Link="ScriptedChatClient.cs" />
//   </ItemGroup>
//
// Three things it records that turned out to matter repeatedly:
//
//   * Instructions arrive on ChatOptions.Instructions, NOT as a system
//     ChatMessage. AsAIAgent puts them there. Tests that look for a system
//     message find nothing and read as "the agent lost its persona".
//   * The inbound message list is how you prove a pipeline actually shares
//     its conversation — the claim at the heart of chapters 12, 14 and 15.
//   * Call start/end timestamps are how you prove chapter 13's agents really
//     do run concurrently rather than one after another.
//
// Not thread-safe by accident: chapter 13 runs three agents at once, so the
// recording side is explicitly locked.

using System.Runtime.CompilerServices;
using Microsoft.Extensions.AI;

namespace MafV1.Shared.Testing;

/// <summary>One recorded call into the fake model.</summary>
/// <param name="Instructions">The agent instructions, from ChatOptions.</param>
/// <param name="Messages">Inbound messages, flattened to "[role] text" lines.</param>
/// <param name="Tools">Names of the tools advertised on this call, if any.</param>
/// <param name="ToolDescriptions">Their descriptions, in the same order.</param>
/// <param name="StartedAt">When the call began.</param>
/// <param name="EndedAt">When the call returned.</param>
public sealed record ScriptedCall(
    string Instructions,
    IReadOnlyList<string> Messages,
    IReadOnlyList<string> Tools,
    IReadOnlyList<string> ToolDescriptions,
    DateTimeOffset StartedAt,
    DateTimeOffset EndedAt)
{
    /// <summary>The inbound messages as one blob, for substring assertions.</summary>
    public string Text => string.Join("\n", Messages);

    /// <summary>
    /// The name of the single tool whose description contains
    /// <paramref name="descriptionFragment"/>.
    /// </summary>
    /// <remarks>
    /// Chapter 14 needs this because MAF synthesises handoff tools with
    /// positional names (handoff_to_1, handoff_to_2) — the description is the
    /// only thing that identifies the target, for the test and for the model
    /// alike. Selecting by name would encode the wiring order the test is
    /// supposed to be checking.
    /// </remarks>
    public string ToolNamed(string descriptionFragment)
    {
        for (int i = 0; i < ToolDescriptions.Count; i++)
        {
            if (ToolDescriptions[i].Contains(descriptionFragment, StringComparison.OrdinalIgnoreCase))
            {
                return Tools[i];
            }
        }

        throw new InvalidOperationException(
            $"no advertised tool whose description contains '{descriptionFragment}'. Saw: "
            + string.Join(" | ", Tools.Zip(ToolDescriptions, (n, d) => $"{n}={d}")));
    }
}

/// <summary>
/// An <see cref="IChatClient"/> that answers from a script instead of a model.
/// </summary>
public sealed class ScriptedChatClient : IChatClient
{
    private readonly Queue<string> _queue;
    private readonly Func<ScriptedCall, IList<AIContent>>? _responder;
    private readonly List<ScriptedCall> _calls = new();
    private readonly object _gate = new();
    private int _served;

    /// <summary>Answer with these strings, in order, then empty string.</summary>
    public ScriptedChatClient(params string[] responses) => _queue = new Queue<string>(responses);

    /// <summary>
    /// Answer by inspecting the call. Use when the order agents run in is not
    /// fixed — chapter 13's three agents race, so a positional queue would
    /// hand the marketer the researcher's line about one run in six.
    /// </summary>
    public ScriptedChatClient(Func<ScriptedCall, string> responder)
        : this(call => new List<AIContent> { new TextContent(responder(call)) })
    {
    }

    /// <summary>
    /// Answer with arbitrary content — the overload chapter 14 needs, because
    /// a handoff is a synthesised <see cref="FunctionCallContent"/>, not text.
    /// Build one with <see cref="ToolCall"/>.
    /// </summary>
    public ScriptedChatClient(Func<ScriptedCall, IList<AIContent>> responder)
    {
        _queue = new Queue<string>();
        _responder = responder;
    }

    /// <summary>A single tool call, as a model would emit it.</summary>
    public static IList<AIContent> ToolCall(string name, object? arguments = null) =>
        new List<AIContent>
        {
            new FunctionCallContent(
                callId: Guid.NewGuid().ToString("N"),
                name: name,
                arguments: arguments as IDictionary<string, object?> ?? new Dictionary<string, object?>()),
        };

    /// <summary>Plain assistant text.</summary>
    public static IList<AIContent> Text(string text) => new List<AIContent> { new TextContent(text) };

    /// <summary>Throw once this many calls have been served. 0 disables.</summary>
    public int ThrowAfter { get; init; }

    /// <summary>Artificial latency per call. Chapter 13 uses it to prove overlap.</summary>
    public TimeSpan Delay { get; init; } = TimeSpan.Zero;

    /// <summary>Every call, in completion order.</summary>
    public IReadOnlyList<ScriptedCall> Calls
    {
        get { lock (_gate) { return _calls.ToList(); } }
    }

    /// <summary>The call whose instructions contain <paramref name="fragment"/>.</summary>
    public ScriptedCall CallFor(string fragment) =>
        Calls.SingleOrDefault(c => c.Instructions.Contains(fragment, StringComparison.OrdinalIgnoreCase))
        ?? throw new InvalidOperationException(
            $"no call with instructions containing '{fragment}'. Saw: "
            + string.Join(" | ", Calls.Select(c => c.Instructions)));

    /// <summary>
    /// True when at least two calls were in flight at the same moment — the
    /// only honest way to assert "these ran concurrently".
    /// </summary>
    public bool HadOverlappingCalls()
    {
        List<ScriptedCall> calls = Calls.OrderBy(c => c.StartedAt).ToList();
        for (int i = 1; i < calls.Count; i++)
        {
            if (calls[i].StartedAt < calls[i - 1].EndedAt)
            {
                return true;
            }
        }

        return false;
    }

    private async Task<IList<AIContent>> AnswerAsync(
        IEnumerable<ChatMessage> messages,
        ChatOptions? options,
        CancellationToken cancellationToken)
    {
        DateTimeOffset started = DateTimeOffset.UtcNow;
        List<ChatMessage> inbound = messages.ToList();

        if (Delay > TimeSpan.Zero)
        {
            await Task.Delay(Delay, cancellationToken).ConfigureAwait(false);
        }

        var call = new ScriptedCall(
            Instructions: options?.Instructions ?? string.Empty,
            Messages: inbound.Select(m => $"[{m.Role}] {m.Text}").ToList(),
            Tools: options?.Tools?.Select(t => t.Name).ToList() ?? new List<string>(),
            ToolDescriptions: options?.Tools?.Select(t => t.Description ?? string.Empty).ToList() ?? new List<string>(),
            StartedAt: started,
            EndedAt: DateTimeOffset.UtcNow);

        int served;
        lock (_gate)
        {
            _calls.Add(call);
            served = ++_served;
        }

        if (ThrowAfter > 0 && served > ThrowAfter)
        {
            throw new InvalidOperationException($"scripted failure on call {served}");
        }

        if (_responder is not null)
        {
            return _responder(call);
        }

        lock (_gate)
        {
            return Text(_queue.Count > 0 ? _queue.Dequeue() : string.Empty);
        }
    }

    public async Task<ChatResponse> GetResponseAsync(
        IEnumerable<ChatMessage> messages,
        ChatOptions? options = null,
        CancellationToken cancellationToken = default)
    {
        IList<AIContent> content = await AnswerAsync(messages, options, cancellationToken).ConfigureAwait(false);
        return new ChatResponse(new ChatMessage(ChatRole.Assistant, content));
    }

    public async IAsyncEnumerable<ChatResponseUpdate> GetStreamingResponseAsync(
        IEnumerable<ChatMessage> messages,
        ChatOptions? options = null,
        [EnumeratorCancellation] CancellationToken cancellationToken = default)
    {
        IList<AIContent> content = await AnswerAsync(messages, options, cancellationToken).ConfigureAwait(false);
        yield return new ChatResponseUpdate(ChatRole.Assistant, content);
    }

    public object? GetService(Type serviceType, object? serviceKey = null) => null;

    public void Dispose() { }
}
