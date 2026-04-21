using Dapper;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;

namespace ECommerceAgents.Orchestrator.Routes;

/// <summary>
/// <c>/api/seller/*</c> endpoints — gated on the <c>seller</c> role.
/// Returns only the caller's own products, orders containing those
/// products, and aggregate sales statistics.
/// </summary>
public static class SellerRoutes
{
    public static IEndpointRouteBuilder MapSellerRoutes(this IEndpointRouteBuilder routes)
    {
        routes.MapGet("/api/seller/products", ListProducts);
        routes.MapGet("/api/seller/orders", ListOrders);
        routes.MapGet("/api/seller/stats", GetStats);
        return routes;
    }

    private static IResult? RequireSeller()
    {
        var email = RequestContext.CurrentUserEmail;
        if (string.IsNullOrEmpty(email)) return Results.Unauthorized();
        var role = RequestContext.CurrentUserRole;
        if (!string.Equals(role, "seller", StringComparison.OrdinalIgnoreCase)
            && !string.Equals(role, "admin", StringComparison.OrdinalIgnoreCase))
        {
            return Results.Json(new { detail = "Seller role required" }, statusCode: 403);
        }
        return null;
    }

    // ─────────────────────── products ────────────────────────

    private static async Task<IResult> ListProducts(
        DatabasePool pool,
        string? category = null,
        int limit = 50,
        int offset = 0
    )
    {
        var guard = RequireSeller();
        if (guard is not null) return guard;

        var userId = await UserResolver.ResolveUserIdAsync(pool, RequestContext.CurrentUserEmail);
        if (userId is null) return Results.Unauthorized();

        int clampedLimit = Math.Clamp(limit, 1, 200);
        int clampedOffset = Math.Max(0, offset);

        await using var conn = await pool.OpenAsync();
        var rows = (await conn.QueryAsync(
            @"SELECT p.id, p.name, p.description, p.category, p.brand, p.price,
                     p.original_price, p.image_url, p.rating, p.review_count, p.is_active
              FROM products p
              WHERE p.seller_id = @u
                AND (@category::text IS NULL OR p.category = @category)
              ORDER BY p.created_at DESC
              LIMIT @limit OFFSET @offset",
            new { u = userId, category, limit = clampedLimit, offset = clampedOffset }
        )).Select(r => new
        {
            id = ((Guid)r.id).ToString(),
            name = (string)r.name,
            description = r.description is null ? "" : ((string)r.description).Length > 200
                ? ((string)r.description)[..200]
                : (string)r.description,
            category = (string?)r.category,
            brand = (string?)r.brand,
            price = (decimal)r.price,
            original_price = r.original_price is null ? (decimal?)null : (decimal)r.original_price,
            image_url = (string?)r.image_url,
            rating = (decimal)r.rating,
            review_count = Convert.ToInt32(r.review_count),
            is_active = (bool)r.is_active,
        }).ToList();

        var total = await conn.ExecuteScalarAsync<long>(
            @"SELECT COUNT(*) FROM products p
              WHERE p.seller_id = @u AND (@category::text IS NULL OR p.category = @category)",
            new { u = userId, category }
        );

        return Results.Ok(new { products = rows, total });
    }

    // ─────────────────────── orders ──────────────────────────

    private static async Task<IResult> ListOrders(
        DatabasePool pool,
        string? status = null,
        int limit = 20,
        int offset = 0
    )
    {
        var guard = RequireSeller();
        if (guard is not null) return guard;

        var userId = await UserResolver.ResolveUserIdAsync(pool, RequestContext.CurrentUserEmail);
        if (userId is null) return Results.Unauthorized();

        int clampedLimit = Math.Clamp(limit, 1, 200);
        int clampedOffset = Math.Max(0, offset);

        await using var conn = await pool.OpenAsync();
        var rows = (await conn.QueryAsync(
            @"SELECT o.id, o.status, o.total, o.created_at,
                     buyer.name AS buyer_name, buyer.email AS buyer_email,
                     COUNT(oi.id) AS item_count
              FROM orders o
              JOIN order_items oi ON oi.order_id = o.id
              JOIN products p ON oi.product_id = p.id
              JOIN users buyer ON o.user_id = buyer.id
              WHERE p.seller_id = @u
                AND (@status::text IS NULL OR o.status = @status)
              GROUP BY o.id, o.status, o.total, o.created_at, buyer.name, buyer.email
              ORDER BY o.created_at DESC
              LIMIT @limit OFFSET @offset",
            new { u = userId, status, limit = clampedLimit, offset = clampedOffset }
        )).Select(r => new
        {
            id = ((Guid)r.id).ToString(),
            status = (string)r.status,
            total = (decimal)r.total,
            date = ((DateTime)r.created_at).ToString("o"),
            buyer_name = (string?)r.buyer_name,
            buyer_email = (string?)r.buyer_email,
            item_count = Convert.ToInt64(r.item_count),
        }).ToList();

        var total = await conn.ExecuteScalarAsync<long>(
            @"SELECT COUNT(DISTINCT o.id)
              FROM orders o
              JOIN order_items oi ON oi.order_id = o.id
              JOIN products p ON oi.product_id = p.id
              WHERE p.seller_id = @u AND (@status::text IS NULL OR o.status = @status)",
            new { u = userId, status }
        );

        return Results.Ok(new { orders = rows, total });
    }

    // ─────────────────────── stats ───────────────────────────

    private static async Task<IResult> GetStats(DatabasePool pool)
    {
        var guard = RequireSeller();
        if (guard is not null) return guard;

        var userId = await UserResolver.ResolveUserIdAsync(pool, RequestContext.CurrentUserEmail);
        if (userId is null) return Results.Unauthorized();

        await using var conn = await pool.OpenAsync();
        var productCount = await conn.ExecuteScalarAsync<long>(
            "SELECT COUNT(*) FROM products WHERE seller_id = @u", new { u = userId });
        var totalRevenue = await conn.ExecuteScalarAsync<decimal>(
            @"SELECT COALESCE(SUM(oi.subtotal), 0)
              FROM order_items oi
              JOIN products p ON oi.product_id = p.id
              WHERE p.seller_id = @u",
            new { u = userId });
        var orderCount = await conn.ExecuteScalarAsync<long>(
            @"SELECT COUNT(DISTINCT o.id)
              FROM orders o
              JOIN order_items oi ON oi.order_id = o.id
              JOIN products p ON oi.product_id = p.id
              WHERE p.seller_id = @u",
            new { u = userId });
        var avgRating = await conn.ExecuteScalarAsync<decimal?>(
            "SELECT COALESCE(AVG(rating), 0) FROM products WHERE seller_id = @u",
            new { u = userId });

        return Results.Ok(new
        {
            product_count = productCount,
            total_revenue = totalRevenue,
            order_count = orderCount,
            avg_rating = Math.Round(avgRating ?? 0m, 2),
        });
    }
}
