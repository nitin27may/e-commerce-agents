using System.Collections.Concurrent;

namespace ECommerceAgents.Shared.Sessions;

/// <summary>
/// Ephemeral per-process history store. Useful in tests and local dev
/// when you don't want a Postgres dependency for session replay.
/// </summary>
public sealed class InMemorySessionHistoryProvider : ISessionHistoryProvider
{
    private readonly ConcurrentDictionary<string, List<StoredMessage>> _store = new();

    public Task<List<StoredMessage>> GetMessagesAsync(string? sessionId, CancellationToken ct = default)
    {
        if (string.IsNullOrEmpty(sessionId))
        {
            return Task.FromResult(new List<StoredMessage>());
        }

        var snapshot = _store.TryGetValue(sessionId, out var bucket)
            ? bucket.ToList()
            : new List<StoredMessage>();
        return Task.FromResult(snapshot);
    }

    public Task SaveMessagesAsync(
        string? sessionId,
        IReadOnlyList<StoredMessage> messages,
        CancellationToken ct = default
    )
    {
        if (string.IsNullOrEmpty(sessionId) || messages.Count == 0)
        {
            return Task.CompletedTask;
        }

        var bucket = _store.GetOrAdd(sessionId, _ => []);
        lock (bucket)
        {
            bucket.AddRange(messages);
        }
        return Task.CompletedTask;
    }
}
