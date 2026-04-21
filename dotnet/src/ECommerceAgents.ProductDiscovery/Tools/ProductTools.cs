using Dapper;
using ECommerceAgents.Shared.Data;
using Microsoft.Extensions.AI;
using System.ComponentModel;
using System.Text.Json;

namespace ECommerceAgents.ProductDiscovery.Tools;

/// <summary>
/// MAF tools for product search, details and comparison. Mirrors
/// Python's <c>agents/product_discovery/tools.py</c>; the SQL is the
/// same so both stacks answer equivalent queries against the same
/// schema.
/// </summary>
public sealed class ProductTools(DatabasePool pool)
{
    private readonly DatabasePool _pool = pool;

    public IEnumerable<AITool> All() => new AITool[]
    {
        AIFunctionFactory.Create(SearchProducts, nameof(SearchProducts)),
        AIFunctionFactory.Create(GetProductDetails, nameof(GetProductDetails)),
        AIFunctionFactory.Create(CompareProducts, nameof(CompareProducts)),
        AIFunctionFactory.Create(GetTrendingProducts, nameof(GetTrendingProducts)),
    };

    [Description("Search the product catalog using natural language. Supports filtering by category, price range and rating.")]
    public async Task<List<ProductSummary>> SearchProducts(
        [Description("Natural language search query (optional if using category filter)")] string? query = null,
        [Description("Filter by category: Electronics, Clothing, Home, Sports, Books")] string? category = null,
        [Description("Minimum price filter")] decimal? minPrice = null,
        [Description("Maximum price filter")] decimal? maxPrice = null,
        [Description("Minimum rating (1-5)")] decimal? minRating = null,
        [Description("Sort by: price_asc, price_desc, rating, newest")] string? sortBy = null,
        [Description("Max results to return")] int limit = 10
    )
    {
        var conditions = new List<string> { "p.is_active = TRUE" };
        var parameters = new DynamicParameters();
        var idx = 1;

        if (!string.IsNullOrWhiteSpace(query))
        {
            foreach (var word in query.Split(' ', StringSplitOptions.RemoveEmptyEntries).Where(w => w.Length >= 2))
            {
                conditions.Add($"(p.name ILIKE @p{idx} OR p.description ILIKE @p{idx})");
                parameters.Add($"p{idx}", $"%{word}%");
                idx++;
            }
        }

        void Add<T>(string column, T? value, string op = "=")
            where T : struct
        {
            if (value.HasValue)
            {
                conditions.Add($"{column} {op} @p{idx}");
                parameters.Add($"p{idx}", value.Value);
                idx++;
            }
        }

        if (!string.IsNullOrWhiteSpace(category))
        {
            conditions.Add($"p.category = @p{idx}");
            parameters.Add($"p{idx}", category);
            idx++;
        }

        Add("p.price", minPrice, ">=");
        Add("p.price", maxPrice, "<=");
        Add("p.rating", minRating, ">=");

        var order = sortBy switch
        {
            "price_asc" => "p.price ASC",
            "price_desc" => "p.price DESC",
            "rating" => "p.rating DESC",
            "newest" => "p.created_at DESC",
            _ => "p.rating DESC, p.review_count DESC",
        };

        var sql = $@"
            SELECT p.id, p.name, p.description, p.category, p.brand, p.price,
                   p.original_price, p.rating, p.review_count
            FROM products p
            WHERE {string.Join(" AND ", conditions)}
            ORDER BY {order}
            LIMIT {limit}";

        await using var conn = await _pool.OpenAsync();
        var rows = await conn.QueryAsync(sql, parameters);
        return rows.Select(r => new ProductSummary(
            Id: ((Guid)r.id).ToString(),
            Name: (string)r.name,
            Description: Truncate((string?)r.description ?? "", 150),
            Category: (string)r.category,
            Brand: (string?)r.brand ?? "",
            Price: (decimal)r.price,
            OriginalPrice: r.original_price is null ? null : (decimal)r.original_price,
            OnSale: r.original_price is not null && (decimal)r.price < (decimal)r.original_price,
            Rating: (decimal)r.rating,
            ReviewCount: (int)r.review_count
        )).ToList();
    }

    [Description("Get complete details for a specific product including full specs.")]
    public async Task<ProductDetails?> GetProductDetails(
        [Description("UUID of the product")] string productId
    )
    {
        if (!Guid.TryParse(productId, out var id))
        {
            return null;
        }

        await using var conn = await _pool.OpenAsync();
        var row = await conn.QueryFirstOrDefaultAsync(
            @"SELECT id, name, description, category, brand, price, original_price,
                     image_url, rating, review_count, specs
              FROM products WHERE id = @id",
            new { id }
        );
        if (row is null)
        {
            return null;
        }

        Dictionary<string, JsonElement>? specs = null;
        if (row.specs is not null)
        {
            var raw = row.specs is string s ? s : row.specs.ToString();
            if (!string.IsNullOrWhiteSpace(raw))
            {
                specs = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(raw);
            }
        }

        return new ProductDetails(
            Id: ((Guid)row.id).ToString(),
            Name: (string)row.name,
            Description: (string?)row.description ?? "",
            Category: (string)row.category,
            Brand: (string?)row.brand ?? "",
            Price: (decimal)row.price,
            OriginalPrice: row.original_price is null ? null : (decimal)row.original_price,
            OnSale: row.original_price is not null && (decimal)row.price < (decimal)row.original_price,
            Rating: (decimal)row.rating,
            ReviewCount: (int)row.review_count,
            ImageUrl: (string?)row.image_url,
            Specs: specs
        );
    }

    [Description("Compare 2-3 products side by side on key attributes.")]
    public async Task<List<ProductDetails>> CompareProducts(
        [Description("List of 2-3 product UUIDs")] List<string> productIds
    )
    {
        if (productIds.Count < 2 || productIds.Count > 3)
        {
            return [];
        }

        var results = new List<ProductDetails>(productIds.Count);
        foreach (var pid in productIds)
        {
            var details = await GetProductDetails(pid);
            if (details is not null)
            {
                results.Add(details);
            }
        }
        return results;
    }

    [Description("Get trending products based on recent order volume.")]
    public async Task<List<TrendingProduct>> GetTrendingProducts(
        [Description("Optional category filter")] string? category = null,
        [Description("Trending period in days")] int days = 30,
        [Description("Max results")] int limit = 10
    )
    {
        var sql = @"
            SELECT p.id, p.name, p.category, p.brand, p.price, p.rating,
                   COUNT(oi.id) AS order_count,
                   COALESCE(SUM(oi.quantity), 0) AS units_sold
            FROM products p
            JOIN order_items oi ON oi.product_id = p.id
            JOIN orders o ON oi.order_id = o.id
            WHERE o.created_at >= NOW() - (@days || ' days')::interval
              AND (@category::text IS NULL OR p.category = @category)
            GROUP BY p.id, p.name, p.category, p.brand, p.price, p.rating
            ORDER BY units_sold DESC
            LIMIT @limit";

        await using var conn = await _pool.OpenAsync();
        var rows = await conn.QueryAsync(sql, new { days = days.ToString(), category, limit });
        return rows.Select(r => new TrendingProduct(
            Id: ((Guid)r.id).ToString(),
            Name: (string)r.name,
            Category: (string)r.category,
            Brand: (string?)r.brand ?? "",
            Price: (decimal)r.price,
            Rating: (decimal)r.rating,
            OrderCount: Convert.ToInt32(r.order_count),
            UnitsSold: Convert.ToInt32(r.units_sold)
        )).ToList();
    }

    private static string Truncate(string value, int max) =>
        value.Length <= max ? value : value[..max];
}

public sealed record ProductSummary(
    string Id,
    string Name,
    string Description,
    string Category,
    string Brand,
    decimal Price,
    decimal? OriginalPrice,
    bool OnSale,
    decimal Rating,
    int ReviewCount
);

public sealed record ProductDetails(
    string Id,
    string Name,
    string Description,
    string Category,
    string Brand,
    decimal Price,
    decimal? OriginalPrice,
    bool OnSale,
    decimal Rating,
    int ReviewCount,
    string? ImageUrl,
    Dictionary<string, JsonElement>? Specs
);

public sealed record TrendingProduct(
    string Id,
    string Name,
    string Category,
    string Brand,
    decimal Price,
    decimal Rating,
    int OrderCount,
    int UnitsSold
);
