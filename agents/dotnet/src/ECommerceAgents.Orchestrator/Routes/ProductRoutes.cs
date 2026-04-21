using Dapper;
using ECommerceAgents.Shared.Data;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;
using System.Text.Json;

namespace ECommerceAgents.Orchestrator.Routes;

/// <summary>
/// <c>/api/products</c> catalogue endpoints. Parity with Python's
/// <c>list_products</c> + <c>get_product</c>.
/// </summary>
public static class ProductRoutes
{
    private const int MaxLimit = 200;

    private static readonly IReadOnlyDictionary<string, string> SortClauses =
        new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            ["price_asc"] = "p.price ASC",
            ["price_desc"] = "p.price DESC",
            ["rating"] = "p.rating DESC",
            ["newest"] = "p.created_at DESC",
            ["name"] = "p.name ASC",
        };

    public static IEndpointRouteBuilder MapProductRoutes(this IEndpointRouteBuilder routes)
    {
        routes.MapGet("/api/products", ListProducts);
        routes.MapGet("/api/products/{id}", GetProduct);
        return routes;
    }

    private static async Task<IResult> ListProducts(
        HttpRequest request,
        DatabasePool pool,
        string? category = null,
        decimal? minPrice = null,
        decimal? maxPrice = null,
        string? search = null,
        string sort = "rating",
        int limit = 50,
        int offset = 0
    )
    {
        var clampedLimit = Math.Clamp(limit, 1, MaxLimit);
        var clampedOffset = Math.Max(0, offset);
        var orderClause = SortClauses.TryGetValue(sort, out var c) ? c : "p.rating DESC";

        var conditions = new List<string> { "p.is_active = TRUE" };
        var parameters = new DynamicParameters();

        if (!string.IsNullOrWhiteSpace(category))
        {
            conditions.Add("p.category = @category");
            parameters.Add("category", category);
        }
        if (minPrice.HasValue)
        {
            conditions.Add("p.price >= @min_price");
            parameters.Add("min_price", minPrice.Value);
        }
        if (maxPrice.HasValue)
        {
            conditions.Add("p.price <= @max_price");
            parameters.Add("max_price", maxPrice.Value);
        }
        if (!string.IsNullOrWhiteSpace(search))
        {
            conditions.Add("(p.name ILIKE @q OR p.description ILIKE @q)");
            parameters.Add("q", $"%{search}%");
        }
        parameters.Add("limit", clampedLimit);
        parameters.Add("offset", clampedOffset);
        var where = string.Join(" AND ", conditions);

        await using var conn = await pool.OpenAsync();
        var rows = (await conn.QueryAsync(
            $@"SELECT p.id, p.name, p.description, p.category, p.brand, p.price,
                      p.original_price, p.image_url, p.rating, p.review_count
               FROM products p
               WHERE {where}
               ORDER BY {orderClause}
               LIMIT @limit OFFSET @offset",
            parameters
        )).ToList();

        var total = await conn.ExecuteScalarAsync<int>(
            $"SELECT COUNT(*) FROM products p WHERE {where}",
            parameters
        );
        var categories = (await conn.QueryAsync<string>(
            "SELECT DISTINCT category FROM products WHERE is_active = TRUE ORDER BY category"
        )).ToList();

        var products = rows.Select(r => new
        {
            id = ((Guid)r.id).ToString(),
            name = (string)r.name,
            description = Truncate((string?)r.description ?? "", 200),
            category = (string)r.category,
            brand = (string?)r.brand,
            price = (decimal)r.price,
            original_price = r.original_price is null ? null : (decimal?)r.original_price,
            image_url = (string?)r.image_url,
            rating = (decimal)r.rating,
            review_count = (int)r.review_count,
        });
        return Results.Ok(new { products, total, categories });
    }

    private static async Task<IResult> GetProduct(string id, DatabasePool pool)
    {
        if (!Guid.TryParse(id, out var pid))
        {
            return Results.NotFound(new { detail = "Product not found" });
        }

        await using var conn = await pool.OpenAsync();
        var row = await conn.QueryFirstOrDefaultAsync(
            @"SELECT p.id, p.name, p.description, p.category, p.brand, p.price,
                     p.original_price, p.image_url, p.rating, p.review_count, p.specs
              FROM products p WHERE p.id = @pid",
            new { pid }
        );
        if (row is null)
        {
            return Results.NotFound(new { detail = "Product not found" });
        }

        var stock = (await conn.QueryAsync(
            @"SELECT w.name, w.region, wi.quantity
              FROM warehouse_inventory wi
              JOIN warehouses w ON wi.warehouse_id = w.id
              WHERE wi.product_id = @pid",
            new { pid }
        )).Select(s => new
        {
            name = (string)s.name,
            region = (string)s.region,
            quantity = (int)s.quantity,
        }).ToList();
        var totalStock = stock.Sum(s => s.quantity);

        var reviews = (await conn.QueryAsync(
            @"SELECT r.id, r.rating, r.title, r.body, r.verified_purchase, r.created_at,
                     u.name AS reviewer_name
              FROM reviews r
              JOIN users u ON r.user_id = u.id
              WHERE r.product_id = @pid
              ORDER BY r.created_at DESC LIMIT 10",
            new { pid }
        )).Select(rv => new
        {
            id = ((Guid)rv.id).ToString(),
            rating = (int)rv.rating,
            title = (string?)rv.title,
            body = (string)rv.body,
            verified = (bool)rv.verified_purchase,
            reviewer = (string)rv.reviewer_name,
            date = ((DateTime)rv.created_at).ToString("o"),
        }).ToList();

        var dist = (await conn.QueryAsync(
            "SELECT rating, COUNT(*) AS count FROM reviews WHERE product_id = @pid GROUP BY rating ORDER BY rating",
            new { pid }
        )).ToDictionary(
            r => ((int)r.rating).ToString(),
            r => (int)r.count
        );

        Dictionary<string, JsonElement>? specs = null;
        if (row.specs is not null)
        {
            var raw = row.specs is string s ? s : row.specs.ToString();
            if (!string.IsNullOrWhiteSpace(raw))
            {
                specs = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(raw);
            }
        }

        return Results.Ok(new
        {
            id = ((Guid)row.id).ToString(),
            name = (string)row.name,
            description = (string?)row.description,
            category = (string)row.category,
            brand = (string?)row.brand,
            price = (decimal)row.price,
            original_price = row.original_price is null ? null : (decimal?)row.original_price,
            image_url = (string?)row.image_url,
            rating = (decimal)row.rating,
            review_count = (int)row.review_count,
            specs = specs ?? new Dictionary<string, JsonElement>(),
            in_stock = totalStock > 0,
            total_stock = totalStock,
            warehouses = stock,
            reviews,
            rating_distribution = dist,
        });
    }

    private static string Truncate(string value, int max) =>
        value.Length <= max ? value : value[..max];
}
