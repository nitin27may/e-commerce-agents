using Dapper;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using Microsoft.Extensions.AI;
using System.ComponentModel;

namespace ECommerceAgents.Shared.Tools;

/// <summary>
/// Long-term memory an agent can write as well as read — the .NET twin of Python's
/// <c>shared/tools/memory_tools.py</c> (#19).
/// </summary>
/// <remarks>
/// .NET could already *read* memories: <c>ContextEnricher</c> injects them into context
/// and <c>ProfileRoutes</c> serves them. It could not write one, which made the Profile
/// page's "AI Memory" card dishonest — it tells the user to chat in order to build a
/// profile, and on this backend chatting could never add anything. Every memory visible
/// there had been written by the Python stack against the shared database.
///
/// Scoped to the caller's own identity from <see cref="RequestContext"/> rather than a
/// tool argument, so the model cannot write a memory onto another user's profile by
/// passing a different email — the same rule every other tool here follows.
/// </remarks>
public sealed class MemoryTools(DatabasePool pool)
{
    private readonly DatabasePool _pool = pool;

    /// <summary>Categories Python's tool documents. Anything else is rejected.</summary>
    private static readonly string[] Categories = ["preference", "behavior", "feedback", "context"];

    public IEnumerable<AITool> All() =>
    [
        AgentTool.Create(StoreMemory, nameof(StoreMemory)),
        AgentTool.Create(RecallMemories, nameof(RecallMemories)),
    ];

    [Description("Store a memory about the current user's preferences, behavior, or feedback for future reference.")]
    public async Task<object> StoreMemory(
        [Description("Memory category: preference, behavior, feedback, or context")] string category,
        [Description("The memory content to store")] string content,
        [Description("Importance score from 1 (low) to 10 (high)")] int importance = 5
    )
    {
        var email = RequestContext.CurrentUserEmail;
        if (string.IsNullOrEmpty(email))
        {
            return new { error = "No authenticated user" };
        }

        if (string.IsNullOrWhiteSpace(content))
        {
            return new { error = "content is required" };
        }

        var normalized = category?.Trim().ToLowerInvariant() ?? "";
        if (!Categories.Contains(normalized))
        {
            // Rejected rather than coerced: a memory filed under a category nothing reads
            // is invisible, which is worse than a refusal the model can act on.
            return new { error = $"category must be one of: {string.Join(", ", Categories)}" };
        }

        await using var conn = await _pool.OpenAsync();
        var userId = await conn.ExecuteScalarAsync<Guid?>(
            "SELECT id FROM users WHERE email = @email", new { email });

        if (userId is null)
        {
            return new { error = "User not found" };
        }

        var memoryId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO agent_memories (user_id, category, content, importance)
              VALUES (@userId, @category, @content, @importance) RETURNING id",
            new
            {
                userId,
                category = normalized,
                content,
                importance = Math.Clamp(importance, 1, 10),
            });

        return new { stored = true, memory_id = memoryId.ToString(), category = normalized };
    }

    [Description("Recall stored memories about the current user's preferences and past interactions.")]
    public async Task<object> RecallMemories(
        [Description("Filter by category: preference, behavior, feedback, context")] string? category = null,
        [Description("Max memories to return")] int limit = 10
    )
    {
        var email = RequestContext.CurrentUserEmail;
        if (string.IsNullOrEmpty(email))
        {
            return new { error = "No authenticated user" };
        }

        await using var conn = await _pool.OpenAsync();
        var rows = await conn.QueryAsync(
            @"SELECT m.id, m.category, m.content, m.importance, m.created_at
                FROM agent_memories m
                JOIN users u ON m.user_id = u.id
               WHERE u.email = @email
                 AND m.is_active
                 AND (m.expires_at IS NULL OR m.expires_at > NOW())
                 AND (@category::text IS NULL OR m.category = @category)
               ORDER BY m.importance DESC, m.created_at DESC
               LIMIT @limit",
            new
            {
                email,
                category = string.IsNullOrWhiteSpace(category) ? null : category.Trim().ToLowerInvariant(),
                limit = Math.Clamp(limit, 1, 50),
            });

        return rows.Select(r => new
        {
            memory_id = ((Guid)r.id).ToString(),
            category = (string)r.category,
            content = (string)r.content,
            importance = (int)(short)r.importance,
            created_at = ((DateTime)r.created_at).ToString("o"),
        }).ToList();
    }
}
