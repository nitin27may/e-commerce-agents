using Dapper;
using ECommerceAgents.Shared.Data;

namespace ECommerceAgents.Shared.ContextProviders;

/// <summary>
/// .NET twin of Python's <c>shared/context_providers.py</c>.
/// Composes three providers (user profile, recent orders, agent
/// memories) and emits the same legacy ``user_context`` string the
/// orchestrator prepends to each chat turn — so Python and .NET
/// specialists get the same text in their system prompt given the
/// same DB state.
/// </summary>
public sealed class ContextEnricher(DatabasePool pool)
{
    private readonly DatabasePool _pool = pool;

    /// <summary>Max orders included in the recent-orders block.</summary>
    public int RecentOrdersLimit { get; init; } = 5;

    /// <summary>Max active memories included.</summary>
    public int MemoriesLimit { get; init; } = 10;

    public async Task<EnrichedContext> EnrichAsync(string email, CancellationToken ct = default)
    {
        if (string.IsNullOrEmpty(email) || email == "system")
        {
            return EnrichedContext.Empty;
        }

        await using var conn = await _pool.OpenAsync(ct);

        var profile = await GetUserProfileAsync(conn, email);
        if (profile is null)
        {
            return EnrichedContext.Empty;
        }

        var orders = await GetRecentOrdersAsync(conn, profile.Email, RecentOrdersLimit);
        var memories = await GetMemoriesAsync(conn, profile.Email, MemoriesLimit);
        var userContext = BuildUserContext(profile, orders, memories);

        return new EnrichedContext(
            Profile: profile,
            RecentOrders: orders,
            Memories: memories,
            UserContext: userContext
        );
    }

    // ─────────────────────── providers ───────────────────────

    private static async Task<UserProfile?> GetUserProfileAsync(Npgsql.NpgsqlConnection conn, string email)
    {
        var row = await conn.QueryFirstOrDefaultAsync(
            "SELECT name, role, loyalty_tier, total_spend FROM users WHERE email = @email",
            new { email }
        );
        if (row is null) return null;
        return new UserProfile(
            Name: (string)row.name,
            Email: email,
            Role: (string)row.role,
            LoyaltyTier: (string?)row.loyalty_tier ?? "bronze",
            TotalSpend: (decimal)row.total_spend
        );
    }

    private static async Task<List<RecentOrder>> GetRecentOrdersAsync(
        Npgsql.NpgsqlConnection conn,
        string email,
        int limit
    )
    {
        return (await conn.QueryAsync(
            @"SELECT o.id, o.status, o.total, o.created_at
              FROM orders o
              JOIN users u ON o.user_id = u.id
              WHERE u.email = @email
              ORDER BY o.created_at DESC
              LIMIT @limit",
            new { email, limit }
        )).Select(r => new RecentOrder(
            Id: ((Guid)r.id).ToString(),
            Status: (string)r.status,
            Total: (decimal)r.total,
            CreatedAt: (DateTime)r.created_at
        )).ToList();
    }

    private static async Task<List<MemoryEntry>> GetMemoriesAsync(
        Npgsql.NpgsqlConnection conn,
        string email,
        int limit
    )
    {
        return (await conn.QueryAsync(
            @"SELECT category, content, importance
              FROM agent_memories m
              JOIN users u ON m.user_id = u.id
              WHERE u.email = @email AND m.is_active = TRUE
                AND (m.expires_at IS NULL OR m.expires_at > NOW())
              ORDER BY m.importance DESC, m.created_at DESC
              LIMIT @limit",
            new { email, limit }
        )).Select(r => new MemoryEntry(
            Category: (string)r.category,
            Content: (string)r.content,
            Importance: Convert.ToInt32(r.importance)
        )).ToList();
    }

    // ─────────────────────── user-context assembly ──────────

    /// <summary>
    /// Build the legacy ``user_context`` string. The line-by-line
    /// layout mirrors Python's <c>ECommerceContextProvider.before_run</c>
    /// so both stacks feed identical text into the system prompt.
    /// </summary>
    private static string BuildUserContext(UserProfile profile, List<RecentOrder> orders, List<MemoryEntry> memories)
    {
        var lines = new List<string>
        {
            $"Current user: {profile.Name} ({profile.Email})",
            $"Role: {profile.Role}, Loyalty tier: {profile.LoyaltyTier}, Total spend: ${profile.TotalSpend:F2}",
        };

        if (orders.Count > 0)
        {
            lines.Add($"Recent orders ({orders.Count}):");
            foreach (var order in orders)
            {
                var date = order.CreatedAt.ToString("yyyy-MM-dd");
                lines.Add(
                    $"  - Order {order.Id[..8]}... | {order.Status} | ${order.Total:F2} | {date}"
                );
            }
        }

        if (memories.Count > 0)
        {
            lines.Add("");
            lines.Add("## User Preferences & History");
            foreach (var m in memories)
            {
                lines.Add($"  - [{m.Category}] {m.Content} (importance: {m.Importance})");
            }
        }

        return string.Join("\n", lines);
    }
}

// ─────────────────────── DTOs ───────────────────────

public sealed record UserProfile(
    string Name,
    string Email,
    string Role,
    string LoyaltyTier,
    decimal TotalSpend
);

public sealed record RecentOrder(string Id, string Status, decimal Total, DateTime CreatedAt);

public sealed record MemoryEntry(string Category, string Content, int Importance);

public sealed record EnrichedContext(
    UserProfile? Profile,
    List<RecentOrder> RecentOrders,
    List<MemoryEntry> Memories,
    string UserContext
)
{
    public static readonly EnrichedContext Empty = new(
        Profile: null,
        RecentOrders: [],
        Memories: [],
        UserContext: string.Empty
    );
}
