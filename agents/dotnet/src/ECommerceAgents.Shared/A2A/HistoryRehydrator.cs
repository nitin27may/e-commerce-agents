using Dapper;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;

namespace ECommerceAgents.Shared.A2A;

/// <summary>
/// Mirrors Python's <c>agent_host._rehydrate_history_from_session</c>
/// (audit fix #14): specialists no longer rely solely on a forwarded
/// history payload — when one isn't present, they pull their own recent
/// context straight from Postgres via the session id (= conversation id)
/// header.
/// </summary>
public static class HistoryRehydrator
{
    /// <summary>Kept in lockstep with <c>ChatRoutes.PrepareConversationAsync</c>'s own LIMIT 50.</summary>
    private const int SessionHistoryLimit = 50;

    /// <summary>
    /// Fetches up to <see cref="SessionHistoryLimit"/> recent messages for
    /// <paramref name="sessionId"/> (a conversation UUID). Fail-safe: returns
    /// <c>null</c> on any error (missing/invalid id, no caller identity, DB
    /// failure) so the caller falls back to a no-history run rather than
    /// erroring out — matching Python's behavior exactly.
    /// </summary>
    /// <remarks>
    /// Two properties this query had wrong until #9, both of which look fine
    /// in a short conversation and fail silently in a long one:
    ///
    /// <b>Recency.</b> <c>ORDER BY created_at ASC LIMIT 50</c> takes the
    /// <i>oldest</i> fifty messages, so past that length a follow-up is
    /// answered from the start of the conversation and never sees what was
    /// just said. Python fixed this; .NET kept the original, and had no
    /// over-the-limit test to notice. Ordering is now newest-first inside a
    /// subquery, restored to chronological order outside it.
    ///
    /// <b>Ownership.</b> The session id arrives in an HTTP header, and on the
    /// orchestrator's anonymous path it originates in the request body — so
    /// selecting on <c>conversation_id</c> alone let anyone who knew a
    /// conversation UUID read that conversation. Scoped to the caller's own
    /// email, which every A2A call already forwards as <c>X-User-Email</c>.
    /// An absent identity refuses outright rather than reading unscoped.
    ///
    /// The role filter also moved into SQL. Applied in memory it ran *after*
    /// the LIMIT, so fifty rows containing any non-user/assistant message
    /// yielded fewer than fifty usable ones.
    /// </remarks>
    public static async Task<List<HistoryEntry>?> RehydrateAsync(DatabasePool pool, string sessionId)
    {
        if (string.IsNullOrEmpty(sessionId) || !Guid.TryParse(sessionId, out var conversationId))
        {
            return null;
        }

        var caller = RequestContext.CurrentUserEmail;
        if (string.IsNullOrEmpty(caller))
        {
            return null;
        }

        try
        {
            await using var conn = await pool.OpenAsync();
            var rows = await conn.QueryAsync(
                @"SELECT role, content FROM (
                      SELECT role, content, created_at
                        FROM messages
                       WHERE conversation_id = @id
                         AND role IN ('user', 'assistant')
                         AND EXISTS (
                             SELECT 1 FROM conversations c
                               JOIN users u ON u.id = c.user_id
                              WHERE c.id = @id AND u.email = @caller
                         )
                       ORDER BY created_at DESC
                       LIMIT @limit
                  ) recent
                  ORDER BY created_at ASC",
                new { id = conversationId, limit = SessionHistoryLimit, caller }
            );
            return rows
                .Select(r => new HistoryEntry((string)r.role, (string)r.content))
                .ToList();
        }
        catch
        {
            return null;
        }
    }
}
