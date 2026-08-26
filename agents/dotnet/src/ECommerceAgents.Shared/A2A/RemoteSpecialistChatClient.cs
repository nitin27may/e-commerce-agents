using System.Runtime.CompilerServices;
using ECommerceAgents.Shared.Context;
using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;

namespace ECommerceAgents.Shared.A2A;

/// <summary>
/// An <see cref="IChatClient"/> whose "model" is a specialist agent on the far
/// side of an A2A hop.
/// </summary>
/// <remarks>
/// <para>
/// The .NET counterpart to Python's <c>shared/remote_agent.py::RemoteSpecialistChatClient</c>,
/// and it exists for the same single reason: MAF's handoff orchestration takes
/// <see cref="AIAgent"/> participants, not URLs. Without a client like this
/// there is no way to put a remote specialist into a handoff mesh at all —
/// which is why the .NET side previously hand-rolled a router instead of using
/// the orchestration.
/// </para>
/// <para>
/// The specialist on the other side owns its own system prompt, tools and
/// grounding. This class deliberately adds none of that: it is a transport, and
/// anything it added here would be a second opinion competing with the real
/// agent's.
/// </para>
/// </remarks>
public sealed class RemoteSpecialistChatClient(
    string agentName,
    string url,
    A2AClient client) : IChatClient
{
    /// <summary>
    /// Sends the latest user turn over A2A and returns the reply as one message.
    /// </summary>
    /// <remarks>
    /// Only the last user message is forwarded, and the conversation is passed
    /// separately as history. That mirrors the Python client, and it matters:
    /// the A2A contract is a message plus history, not a flattened transcript.
    /// Flattening would make the specialist re-read the whole conversation as
    /// though the user had just typed it.
    /// </remarks>
    public async Task<ChatResponse> GetResponseAsync(
        IEnumerable<ChatMessage> messages,
        ChatOptions? options = null,
        CancellationToken cancellationToken = default)
    {
        var (message, history) = Split(messages);

        string reply = await client
            .SendAsync(agentName, url, message, history, cancellationToken)
            .ConfigureAwait(false);

        return new ChatResponse(new ChatMessage(ChatRole.Assistant, reply) { AuthorName = agentName });
    }

    public async IAsyncEnumerable<ChatResponseUpdate> GetStreamingResponseAsync(
        IEnumerable<ChatMessage> messages,
        ChatOptions? options = null,
        [EnumeratorCancellation] CancellationToken cancellationToken = default)
    {
        var (message, history) = Split(messages);

        await foreach (string delta in client
            .StreamAsync(agentName, url, message, history, cancellationToken)
            .WithCancellation(cancellationToken)
            .ConfigureAwait(false))
        {
            yield return new ChatResponseUpdate(ChatRole.Assistant, delta) { AuthorName = agentName };
        }
    }

    /// <summary>
    /// Splits the inbound conversation into "the turn to answer" and "what came before".
    /// </summary>
    /// <remarks>
    /// Handoff synchronises context by broadcasting every participant's messages
    /// to the others, so by the time a specialist is handed the conversation it
    /// already carries the triage exchange. The last user message is the live
    /// question; everything before it is history.
    ///
    /// Tool calls and tool results are excluded deliberately — MAF does not
    /// broadcast those between participants, and forwarding them over A2A would
    /// hand a specialist another agent's internal mechanics as though they were
    /// conversation.
    /// </remarks>
    private static (string Message, IReadOnlyList<HistoryEntry> History) Split(
        IEnumerable<ChatMessage> messages)
    {
        List<ChatMessage> ordered = messages
            .Where(m => m.Role == ChatRole.User || m.Role == ChatRole.Assistant)
            .Where(m => !string.IsNullOrWhiteSpace(m.Text))
            .ToList();

        if (ordered.Count == 0)
        {
            return (string.Empty, Array.Empty<HistoryEntry>());
        }

        int lastUser = ordered.FindLastIndex(m => m.Role == ChatRole.User);
        if (lastUser < 0)
        {
            // No user turn at all — an agent-to-agent handoff with only assistant
            // context. Send the most recent text so the specialist has something
            // to act on rather than an empty prompt.
            return (ordered[^1].Text, ordered[..^1].Select(Pair).ToList());
        }

        return (ordered[lastUser].Text, ordered[..lastUser].Select(Pair).ToList());
    }

    private static HistoryEntry Pair(ChatMessage m) =>
        new(m.Role == ChatRole.User ? "user" : "assistant", m.Text);

    public object? GetService(Type serviceType, object? serviceKey = null) => null;

    public void Dispose() { }
}
