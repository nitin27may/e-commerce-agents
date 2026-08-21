using Dapper;
using ECommerceAgents.Shared.Agents;
using ECommerceAgents.Shared.Data;
using Microsoft.Extensions.AI;
using OpenAI.Embeddings;
using System.ComponentModel;
using System.Globalization;
using System.Text.Json;

namespace ECommerceAgents.ProductDiscovery.Tools;

/// <summary>
/// MAF tools for product search, details and comparison. Mirrors
/// Python's <c>agents/product_discovery/tools.py</c>; the SQL is the
/// same so both stacks answer equivalent queries against the same
/// schema.
/// </summary>
public sealed class ProductTools(DatabasePool pool, IEmbeddingProvider embeddingProvider)
{
    private readonly DatabasePool _pool = pool;
    private readonly IEmbeddingProvider _embeddingProvider = embeddingProvider;

    /// <summary>
    /// Whitelist of allowed <c>ORDER BY</c> clauses. Keys come from the
    /// LLM-facing <c>sortBy</c> parameter; values are SQL-safe strings
    /// the tool guarantees it will emit. Any value outside this map
    /// falls through to the default — no user input ever reaches the
    /// SQL string directly.
    /// </summary>
    private static readonly IReadOnlyDictionary<string, string> SortClauses =
        new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            ["price_asc"] = "p.price ASC",
            ["price_desc"] = "p.price DESC",
            ["rating"] = "p.rating DESC",
            ["newest"] = "p.created_at DESC",
        };

    private const string DefaultSortClause = "p.rating DESC, p.review_count DESC";

    /// <summary>Hard ceiling so an LLM-supplied LIMIT can't scan the whole table.</summary>
    private const int MaxLimit = 100;

    private static int ClampLimit(int requested) => Math.Clamp(requested, 1, MaxLimit);

    public IEnumerable<AITool> All() => new AITool[]
    {
        AIFunctionFactory.Create(SearchProducts, nameof(SearchProducts)),
        AIFunctionFactory.Create(GetProductDetails, nameof(GetProductDetails)),
        AIFunctionFactory.Create(CompareProducts, nameof(CompareProducts)),
        AIFunctionFactory.Create(GetTrendingProducts, nameof(GetTrendingProducts)),
        AIFunctionFactory.Create(SemanticSearch, nameof(SemanticSearch)),
        AIFunctionFactory.Create(FindSimilarProducts, nameof(FindSimilarProducts)),
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

        var order = sortBy is not null && SortClauses.TryGetValue(sortBy, out var clause)
            ? clause
            : DefaultSortClause;

        var clampedLimit = ClampLimit(limit);
        parameters.Add("limit", clampedLimit);

        var sql = $@"
            SELECT p.id, p.name, p.description, p.category, p.brand, p.price,
                   p.original_price, p.rating, p.review_count, p.image_url
            FROM products p
            WHERE {string.Join(" AND ", conditions)}
            ORDER BY {order}
            LIMIT @limit";

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
            ReviewCount: (int)r.review_count,
            ImageUrl: (string?)r.image_url
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
        var rows = await conn.QueryAsync(
            sql,
            new { days = days.ToString(), category, limit = ClampLimit(limit) }
        );
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

    [Description("Search products using semantic similarity via pgvector embeddings. Best for vague or descriptive queries like 'something cozy for winter' or 'gift for a tech enthusiast'.")]
    public async Task<List<SemanticSearchResult>> SemanticSearch(
        [Description("Descriptive search query in natural language")] string query,
        [Description("Max results")] int limit = 5
    )
    {
        var vectorText = await EmbedAsync(query);

        await using var conn = await _pool.OpenAsync();
        var rows = await conn.QueryAsync(
            @"SELECT p.id, p.name, p.description, p.category, p.brand, p.price, p.rating, p.image_url,
                     1 - (pe.embedding <=> @vector::vector) AS similarity
              FROM product_embeddings pe
              JOIN products p ON pe.product_id = p.id
              WHERE p.is_active = TRUE
              ORDER BY pe.embedding <=> @vector::vector
              LIMIT @limit",
            new { vector = vectorText, limit }
        );
        return rows.Select(r => new SemanticSearchResult(
            Id: ((Guid)r.id).ToString(),
            Name: (string)r.name,
            Description: Truncate((string?)r.description ?? "", 150),
            Category: (string)r.category,
            Brand: (string?)r.brand ?? "",
            Price: (decimal)r.price,
            Rating: (decimal)r.rating,
            Similarity: Math.Round((double)r.similarity, 3),
            ImageUrl: (string?)r.image_url
        )).ToList();
    }

    [Description("Find products similar to a given product based on embedding similarity.")]
    public async Task<List<SimilarProductResult>> FindSimilarProducts(
        [Description("UUID of the reference product")] string productId,
        [Description("Max results")] int limit = 5
    )
    {
        if (!Guid.TryParse(productId, out var pid))
        {
            return new List<SimilarProductResult> { new(Error: $"No embedding found for product {productId}") };
        }

        await using var conn = await _pool.OpenAsync();

        var refVectorText = await conn.QuerySingleOrDefaultAsync<string?>(
            "SELECT embedding::text FROM product_embeddings WHERE product_id = @pid",
            new { pid }
        );
        if (refVectorText is null)
        {
            return new List<SimilarProductResult> { new(Error: $"No embedding found for product {productId}") };
        }

        var rows = await conn.QueryAsync(
            @"SELECT p.id, p.name, p.category, p.brand, p.price, p.rating,
                     1 - (pe.embedding <=> @vector::vector) AS similarity
              FROM product_embeddings pe
              JOIN products p ON pe.product_id = p.id
              WHERE pe.product_id != @pid AND p.is_active = TRUE
              ORDER BY pe.embedding <=> @vector::vector
              LIMIT @limit",
            new { vector = refVectorText, pid, limit }
        );
        return rows.Select(r => new SimilarProductResult(
            Id: ((Guid)r.id).ToString(),
            Name: (string)r.name,
            Category: (string)r.category,
            Brand: (string?)r.brand ?? "",
            Price: (decimal)r.price,
            Rating: (decimal)r.rating,
            Similarity: Math.Round((double)r.similarity, 3)
        )).ToList();
    }

    /// <summary>Generates a query embedding and renders it as a pgvector text literal
    /// (e.g. <c>[0.1,0.2,...]</c>) so it can be cast with <c>::vector</c> in SQL —
    /// mirrors Python's <c>json.dumps(embedding)</c> passed as <c>$1::vector</c>.
    /// Avoids needing a native Npgsql vector type mapping.</summary>
    private async Task<string> EmbedAsync(string text)
    {
        return await _embeddingProvider.EmbedAsVectorLiteralAsync(text);
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
    int ReviewCount,
    string? ImageUrl
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

public sealed record SemanticSearchResult(
    string Id,
    string Name,
    string Description,
    string Category,
    string Brand,
    decimal Price,
    decimal Rating,
    double Similarity,
    string? ImageUrl
);

/// <summary>
/// Mirrors Python's <c>find_similar_products</c>, which returns
/// <c>[{"error": "..."}]</c> (a one-element list, not an exception) when the
/// reference product has no stored embedding — every field but
/// <see cref="Error"/> is optional so both shapes serialize cleanly.
/// </summary>
public sealed record SimilarProductResult(
    string? Id = null,
    string? Name = null,
    string? Category = null,
    string? Brand = null,
    decimal? Price = null,
    decimal? Rating = null,
    double? Similarity = null,
    string? Error = null
);
