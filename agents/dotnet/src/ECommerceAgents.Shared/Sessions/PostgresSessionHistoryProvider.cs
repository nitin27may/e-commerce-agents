using Dapper;
using ECommerceAgents.Shared.Data;

namespace ECommerceAgents.Shared.Sessions;

/// <summary>
/// Adapter over the existing <c>messages</c> / <c>conversations</c>
/// tables. Session id is interpreted as the conversation UUID; only
/// messages with non-empty content round-trip.
/// </summary>
public sealed class PostgresSessionHistoryProvider(DatabasePool pool) : ISessionHistoryProvider
{
    private readonly DatabasePool _pool = pool;

    public int MaxHistory { get; init; } = 50;

    public async Task<List<StoredMessage>> GetMessagesAsync(string? sessionId, CancellationToken ct = default)
    {
        if (string.IsNullOrEmpty(sessionId) || !Guid.TryParse(sessionId, out var conversationId))
        {
            return [];
        }

        await using var conn = await _pool.OpenAsync(ct);
        var rows = await conn.QueryAsync<(string Role, string Content)>(
            @"SELECT role, content
              FROM messages
              WHERE conversation_id = @id
              ORDER BY created_at ASC
              LIMIT @limit",
            new { id = conversationId, limit = MaxHistory }
        );
        return rows.Select(r => new StoredMessage(r.Role, r.Content)).ToList();
    }

    public async Task SaveMessagesAsync(
        string? sessionId,
        IReadOnlyList<StoredMessage> messages,
        CancellationToken ct = default
    )
    {
        if (string.IsNullOrEmpty(sessionId) || !Guid.TryParse(sessionId, out var conversationId) || messages.Count == 0)
        {
            return;
        }

        await using var conn = await _pool.OpenAsync(ct);
        await using var tx = await conn.BeginTransactionAsync(ct);
        foreach (var msg in messages)
        {
            if (string.IsNullOrEmpty(msg.Content))
            {
                continue;
            }
            await conn.ExecuteAsync(
                @"INSERT INTO messages (conversation_id, role, content)
                  VALUES (@id, @role, @content)",
                new { id = conversationId, role = msg.Role, content = msg.Content },
                tx
            );
        }
        await tx.CommitAsync(ct);
    }
}
