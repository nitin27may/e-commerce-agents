namespace ECommerceAgents.Shared.Sessions;

/// <summary>
/// A single turn in a stored conversation. Mirrors
/// <c>agent_framework.Message</c>'s wire shape (role + text content)
/// without requiring MAF types in every consumer — callers can round-
/// trip the message through any of the three backends (InMemory, File,
/// Postgres) with the same contract.
/// </summary>
public sealed record StoredMessage(string Role, string Content);

/// <summary>
/// Conversation-history storage. Three implementations live in this
/// namespace — <see cref="InMemorySessionHistoryProvider"/>,
/// <see cref="FileSessionHistoryProvider"/>,
/// <see cref="PostgresSessionHistoryProvider"/> — mirroring the Python
/// providers in <c>shared/session.py</c>. Switch between them via
/// <c>MAF_SESSION_BACKEND</c>.
/// </summary>
public interface ISessionHistoryProvider
{
    Task<List<StoredMessage>> GetMessagesAsync(string? sessionId, CancellationToken ct = default);

    Task SaveMessagesAsync(
        string? sessionId,
        IReadOnlyList<StoredMessage> messages,
        CancellationToken ct = default
    );
}
