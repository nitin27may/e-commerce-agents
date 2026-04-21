using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;

namespace ECommerceAgents.TestFixtures;

/// <summary>
/// Deterministic chat client for tests. Queues a list of canned responses; each call pops the next one.
/// Replaces MAF's <c>IChatClient</c> in unit tests so no real LLM call is made.
/// </summary>
/// <remarks>
/// Phase 0 placeholder — the real interface adapter lands in plans/dotnet-port/01-shared.md where
/// MAF's IChatClient contract is available. For now this is a simple queue-based stub.
/// </remarks>
public sealed class FakeChatClient
{
    private readonly Queue<string> _responses = new();

    public int CallCount { get; private set; }

    public IReadOnlyList<string> ReceivedPrompts => _receivedPrompts;
    private readonly List<string> _receivedPrompts = new();

    public FakeChatClient EnqueueResponse(string response)
    {
        _responses.Enqueue(response);
        return this;
    }

    public Task<string> CompleteAsync(string prompt, CancellationToken cancellationToken = default)
    {
        CallCount++;
        _receivedPrompts.Add(prompt);

        if (_responses.Count == 0)
        {
            throw new System.InvalidOperationException(
                "FakeChatClient has no enqueued responses. Call EnqueueResponse before invoking.");
        }

        return Task.FromResult(_responses.Dequeue());
    }
}
