using System.Runtime.CompilerServices;
using ECommerceAgents.Shared.Agents;
using Microsoft.Extensions.AI;

namespace ECommerceAgents.TestFixtures;

/// <summary>
/// A scripted model for the A2A tests: asks for the <c>lookup</c> tool once, then answers
/// <c>Hello world</c> in two chunks and reports usage. The same script as the Python tests' ScriptedModel
/// (<c>tests/a2a_support.py</c>), so a Python client and a .NET client see the same specialist.
/// </summary>
/// <remarks>
/// <see cref="Mode"/> switches the behaviour: <c>boom</c> raises an error whose text must never reach a
/// caller, <c>miss</c> raises a replay-fixture miss, <c>hold</c> parks the first call on
/// <see cref="Gate"/> until a test releases it (or the run is cancelled).
/// </remarks>
public sealed class ScriptedToolChatClient(string mode = "ok") : IChatClient
{
    public const string Answer = "Hello world";

    private readonly object _gate = new();

    public string Mode { get; } = mode;

    /// <summary>Completes to release a <c>hold</c> run.</summary>
    public TaskCompletionSource Gate { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);

    /// <summary>Counts runs that reached the model, so a test can tell a task that never started.</summary>
    public int Started { get; private set; }

    /// <summary>Every call's (role, text) messages, for the equal-model-input assertions.</summary>
    public List<List<(string Role, string Text)>> Seen { get; } = [];

    public async Task<ChatResponse> GetResponseAsync(
        IEnumerable<ChatMessage> messages, ChatOptions? options = null, CancellationToken cancellationToken = default)
    {
        var updates = new List<ChatResponseUpdate>();
        await foreach (var update in GetStreamingResponseAsync(messages, options, cancellationToken))
        {
            updates.Add(update);
        }

        return updates.ToChatResponse();
    }

    public async IAsyncEnumerable<ChatResponseUpdate> GetStreamingResponseAsync(
        IEnumerable<ChatMessage> messages,
        ChatOptions? options = null,
        [EnumeratorCancellation] CancellationToken cancellationToken = default)
    {
        var list = messages.ToList();
        lock (_gate)
        {
            Started++;
            Seen.Add(list.Select(m => (m.Role.Value, m.Text ?? string.Empty)).ToList());
        }

        var hasResult = list.Any(m => m.Contents.OfType<FunctionResultContent>().Any());

        if (Mode == "boom")
        {
            throw new InvalidOperationException("relation \"secret_table\" does not exist");
        }

        if (Mode == "miss")
        {
            throw new ReplayChatClient.FixtureMissingException("abc123", "/fixtures");
        }

        if (Mode == "hold" && !hasResult)
        {
            await Gate.Task.WaitAsync(cancellationToken);
        }

        if (!hasResult)
        {
            yield return new ChatResponseUpdate(
                ChatRole.Assistant,
                [new FunctionCallContent("c1", "lookup", new Dictionary<string, object?> { ["sku"] = "XM5" })]);
            yield break;
        }

        if (Mode == "weird")
        {
            // Characters string-splitting readers treat as line breaks but SSE does not.
            yield return new ChatResponseUpdate(ChatRole.Assistant, "a\u2028b\u2029c\u0085d ");
            yield return new ChatResponseUpdate(ChatRole.Assistant, "line\r\nbreak \U0001F600");
            yield break;
        }

        yield return new ChatResponseUpdate(ChatRole.Assistant, "Hello ");
        yield return new ChatResponseUpdate(ChatRole.Assistant, "world");
        yield return new ChatResponseUpdate(
            ChatRole.Assistant,
            [new UsageContent(new UsageDetails { InputTokenCount = 5, OutputTokenCount = 2 })]);
    }

    public object? GetService(Type serviceType, object? serviceKey = null) => null;

    public void Dispose()
    {
    }
}
