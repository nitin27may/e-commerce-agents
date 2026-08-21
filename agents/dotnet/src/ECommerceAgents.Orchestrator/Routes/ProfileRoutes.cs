using Dapper;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;

namespace ECommerceAgents.Orchestrator.Routes;

/// <summary>
/// <c>GET /api/profile</c>. Returns user profile + loyalty benefits +
/// aggregate order and review counts.
/// </summary>
public static class ProfileRoutes
{
    public static IEndpointRouteBuilder MapProfileRoutes(this IEndpointRouteBuilder routes)
    {
        routes.MapGet("/api/profile", GetProfile);
        routes.MapGet("/api/user/memories", GetUserMemories);
        routes.MapDelete("/api/user/memories/{memoryId}", DeleteUserMemory);
        return routes;
    }

    private static async Task<IResult> GetProfile(DatabasePool pool)
    {
        var email = RequestContext.CurrentUserEmail;
        if (string.IsNullOrEmpty(email)) return Results.Unauthorized();

        await using var conn = await pool.OpenAsync();
        var row = await conn.QueryFirstOrDefaultAsync(
            @"SELECT u.id, u.email, u.name, u.role, u.loyalty_tier, u.total_spend, u.created_at,
                     lt.discount_pct, lt.free_shipping_threshold, lt.priority_support
              FROM users u
              LEFT JOIN loyalty_tiers lt ON lt.name = u.loyalty_tier
              WHERE u.email = @email",
            new { email }
        );
        if (row is null)
        {
            return Results.NotFound(new { detail = "User not found" });
        }

        var orderCount = await conn.ExecuteScalarAsync<long>(
            "SELECT COUNT(*) FROM orders o JOIN users u ON o.user_id = u.id WHERE u.email = @email",
            new { email }
        );
        var reviewCount = await conn.ExecuteScalarAsync<long>(
            "SELECT COUNT(*) FROM reviews r JOIN users u ON r.user_id = u.id WHERE u.email = @email",
            new { email }
        );

        return Results.Ok(new
        {
            id = ((Guid)row.id).ToString(),
            email = (string)row.email,
            name = (string?)row.name,
            role = (string?)row.role,
            loyalty_tier = (string?)row.loyalty_tier,
            total_spend = (decimal)row.total_spend,
            member_since = ((DateTime)row.created_at).ToString("o"),
            order_count = orderCount,
            review_count = reviewCount,
            tier_benefits = new
            {
                discount_pct = row.discount_pct is null ? 0m : (decimal)row.discount_pct,
                free_shipping_threshold = row.free_shipping_threshold is null
                    ? (decimal?)null
                    : (decimal)row.free_shipping_threshold,
                priority_support = row.priority_support is not null && (bool)row.priority_support,
            },
        });
    }

    /// <summary>
    /// <c>GET /api/user/memories</c> — the caller's stored agent memories.
    /// Mirrors Python's <c>get_user_memories</c>.
    /// </summary>
    /// <remarks>
    /// Without this route the profile's "AI Memory" card renders its empty
    /// state — "No memories yet. Chat with the product or review agents to
    /// build your profile." — which on a backend with no memories endpoint is
    /// an instruction that can never come true. The client swallows the 404,
    /// so nothing signals that the card is broken rather than empty. See #33.
    ///
    /// Note this is the read side only. .NET has no agent-callable
    /// <c>store_memory</c> tool yet, so memories here are ones the Python
    /// stack wrote against the same database (tracked separately under #19).
    /// </remarks>
    private static async Task<IResult> GetUserMemories(DatabasePool pool, string? category = null, int limit = 20)
    {
        var email = RequestContext.CurrentUserEmail;
        if (string.IsNullOrEmpty(email))
        {
            return Results.Unauthorized();
        }

        var clamped = Math.Clamp(limit, 1, 100);
        var categoryFilter = string.IsNullOrWhiteSpace(category) ? "" : " AND m.category = @category";

        await using var conn = await pool.OpenAsync();
        var rows = await conn.QueryAsync(
            $@"SELECT m.id, m.category, m.content, m.importance, m.created_at
               FROM agent_memories m
               JOIN users u ON m.user_id = u.id
               WHERE u.email = @email
                 AND m.is_active = TRUE
                 AND (m.expires_at IS NULL OR m.expires_at > NOW())
                 {categoryFilter}
               ORDER BY m.importance DESC, m.created_at DESC
               LIMIT @limit",
            new { email, category, limit = clamped }
        );

        return Results.Ok(rows.Select(r => new
        {
            id = ((Guid)r.id).ToString(),
            category = (string)r.category,
            content = (string)r.content,
            importance = (int)r.importance,
            created_at = ((DateTime)r.created_at).ToString("o"),
        }));
    }

    /// <summary>
    /// <c>DELETE /api/user/memories/{memoryId}</c> — soft-deletes a memory.
    /// Mirrors Python's <c>delete_user_memory</c>, including scoping the
    /// UPDATE by the caller's own user id so one user cannot delete another's.
    /// </summary>
    private static async Task<IResult> DeleteUserMemory(string memoryId, DatabasePool pool)
    {
        var email = RequestContext.CurrentUserEmail;
        if (string.IsNullOrEmpty(email))
        {
            return Results.Unauthorized();
        }

        if (!Guid.TryParse(memoryId, out var memoryGuid))
        {
            return Results.NotFound(new { detail = "Memory not found" });
        }

        await using var conn = await pool.OpenAsync();
        var updated = await conn.ExecuteAsync(
            @"UPDATE agent_memories SET is_active = FALSE
              WHERE id = @id AND user_id = (SELECT id FROM users WHERE email = @email)",
            new { id = memoryGuid, email }
        );

        return updated == 0
            ? Results.NotFound(new { detail = "Memory not found" })
            : Results.Ok(new { deleted = true });
    }
}
