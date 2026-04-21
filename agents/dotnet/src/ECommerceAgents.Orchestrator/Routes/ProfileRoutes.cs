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
}
