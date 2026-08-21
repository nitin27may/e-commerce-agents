using Microsoft.Extensions.AI;
using System.Runtime.CompilerServices;

namespace ECommerceAgents.TestFixtures;

/// <summary>
/// Deterministic <see cref="IChatClient"/> for LLM-less unit tests. Queues a
/// list of canned text responses; each call pops the next one. Wrap with
/// <c>chatClient.AsAIAgent(instructions:, name:)</c> (<c>Microsoft.Agents.AI</c>
/// — same extension method <c>SpecialistAgentFactory.Create</c> uses) to build
/// a real <c>AIAgent</c> backed by this fake, so route/persistence tests never
/// make a real LLM call.
/// </summary>
/// <remarks>
/// Previously a bespoke stub with its own <c>CompleteAsync</c> shape that
/// didn't actually implement <see cref="IChatClient"/> — unusable to
/// construct a real <c>AIAgent</c> and had zero call sites. Rewritten to the
/// real interface (3 methods: <see cref="GetResponseAsync"/>,
/// <see cref="GetStreamingResponseAsync"/>, <see cref="GetService"/>) so it
/// actually delivers on this file's original stated purpose.
/// </remarks>
public sealed class FakeChatClient : IChatClient
{
    private readonly Queue<string> _responses = new();
    private readonly List<IEnumerable<ChatMessage>> _receivedMessages = new();
    private readonly List<ChatOptions?> _receivedOptions = new();

    public int CallCount { get; private set; }
    public IReadOnlyList<IEnumerable<ChatMessage>> ReceivedMessages => _receivedMessages;

    /// <summary>
    /// Runs on every call, inside whatever ambient scope the route set up.
    /// Lets a test observe request-scoped state (e.g.
    /// <c>RequestContext.CurrentSessionId</c>) as the agent actually saw it,
    /// rather than after the scope has been disposed.
    /// </summary>
    public Action? OnCall { get; set; }

    /// <summary>
    /// The <see cref="ChatOptions"/> each call arrived with — this is where
    /// tools and instructions live, so it is the only way a test can prove an
    /// <c>AIContextProvider</c> enriched the request instead of replacing it.
    /// </summary>
    public IReadOnlyList<ChatOptions?> ReceivedOptions => _receivedOptions;

    public FakeChatClient EnqueueResponse(string response)
    {
        _responses.Enqueue(response);
        return this;
    }

    public Task<ChatResponse> GetResponseAsync(
        IEnumerable<ChatMessage> messages,
        ChatOptions? options = null,
        CancellationToken cancellationToken = default
    )
    {
        _receivedMessages.Add(messages);
        _receivedOptions.Add(options);
        var text = NextResponse();
        return Task.FromResult(new ChatResponse(new ChatMessage(ChatRole.Assistant, text)));
    }

    public async IAsyncEnumerable<ChatResponseUpdate> GetStreamingResponseAsync(
        IEnumerable<ChatMessage> messages,
        ChatOptions? options = null,
        [EnumeratorCancellation] CancellationToken cancellationToken = default
    )
    {
        _receivedMessages.Add(messages);
        _receivedOptions.Add(options);
        var text = NextResponse();
        yield return new ChatResponseUpdate(ChatRole.Assistant, text);
        await Task.CompletedTask;
    }

    public object? GetService(Type serviceType, object? serviceKey = null) => null;

    public void Dispose()
    {
    }

    private string NextResponse()
    {
        CallCount++;
        OnCall?.Invoke();
        if (_responses.Count == 0)
        {
            throw new InvalidOperationException(
                "FakeChatClient has no enqueued responses. Call EnqueueResponse before invoking."
            );
        }
        return _responses.Dequeue();
    }
}
