using Dapper;
using ECommerceAgents.Shared.Data;
using Microsoft.Extensions.AI;
using System.ComponentModel;

namespace ECommerceAgents.ReviewSentiment.Tools;

/// <summary>
/// MAF tools for the ReviewSentiment specialist. Mirrors
/// <c>agents/python/review_sentiment/tools.py</c>: pagination + sort
/// for product reviews, aggregate sentiment with rating distribution,
/// keyword search, and side-by-side comparison across 2-3 products.
/// </summary>
/// <remarks>
/// This first slice ports 4 of the 8 Python tools — the four most
/// frequently invoked. The remaining 4 (sentiment_by_topic,
/// sentiment_trend, detect_fake_reviews, draft_seller_response) ship
/// in a follow-up commit.
/// </remarks>
public sealed class ReviewTools(DatabasePool pool)
{
    private readonly DatabasePool _pool = pool;

    private const int MaxLimit = 100;

    private static readonly IReadOnlyDictionary<string, string> SortClauses =
        new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            ["newest"] = "r.created_at DESC",
            ["helpful"] = "r.helpful_count DESC",
            ["rating_high"] = "r.rating DESC, r.created_at DESC",
            ["rating_low"] = "r.rating ASC, r.created_at DESC",
        };

    private const string DefaultSortClause = "r.created_at DESC";

    private static readonly string[] PositiveKeywords =
    [
        "great", "excellent", "love", "perfect", "amazing", "best",
        "quality", "fast", "comfortable", "worth",
    ];

    private static readonly string[] NegativeKeywords =
    [
        "poor", "bad", "terrible", "broken", "slow", "cheap",
        "disappointed", "waste", "defective", "flimsy",
    ];

    public IEnumerable<AITool> All() => new AITool[]
    {
        AIFunctionFactory.Create(GetProductReviews, nameof(GetProductReviews)),
        AIFunctionFactory.Create(AnalyzeSentiment, nameof(AnalyzeSentiment)),
        AIFunctionFactory.Create(SearchReviews, nameof(SearchReviews)),
        AIFunctionFactory.Create(CompareProductReviews, nameof(CompareProductReviews)),
    };

    // ─────────────────────── get_product_reviews ─────────────

    [Description("Get paginated reviews for a product with sorting options.")]
    public async Task<ProductReviewsResult?> GetProductReviews(
        [Description("UUID of the product")] string productId,
        [Description("Sort: newest, helpful, rating_high, rating_low")] string? sortBy = "newest",
        [Description("Max reviews to return (capped at 100)")] int limit = 10,
        [Description("Offset for pagination")] int offset = 0
    )
    {
        if (!Guid.TryParse(productId, out var pid))
        {
            return null;
        }

        var clamped = Math.Clamp(limit, 1, MaxLimit);
        var clampedOffset = Math.Max(0, offset);
        var order = sortBy is not null && SortClauses.TryGetValue(sortBy, out var clause)
            ? clause
            : DefaultSortClause;

        await using var conn = await _pool.OpenAsync();
        var total = await conn.ExecuteScalarAsync<int>(
            "SELECT COUNT(*) FROM reviews WHERE product_id = @pid",
            new { pid }
        );

        var sql = $@"
            SELECT r.id, r.rating, r.title, r.body, r.verified_purchase,
                   r.helpful_count, r.is_flagged, r.created_at,
                   u.name AS reviewer_name
            FROM reviews r
            JOIN users u ON r.user_id = u.id
            WHERE r.product_id = @pid
            ORDER BY {order}
            LIMIT @limit OFFSET @offset";
        var rowsRaw = (await conn.QueryAsync(
            sql,
            new { pid, limit = clamped, offset = clampedOffset }
        )).ToList();

        var reviews = rowsRaw.Select(r => new ReviewEntry(
            Id: ((Guid)r.id).ToString(),
            Rating: (int)r.rating,
            Title: (string?)r.title,
            Body: (string)r.body,
            VerifiedPurchase: (bool)r.verified_purchase,
            HelpfulCount: (int)r.helpful_count,
            IsFlagged: (bool)r.is_flagged,
            Reviewer: (string)r.reviewer_name,
            Date: ((DateTime)r.created_at).ToString("o")
        )).ToList();

        var product = await conn.QueryFirstOrDefaultAsync(
            "SELECT name, rating, review_count FROM products WHERE id = @pid",
            new { pid }
        );

        return new ProductReviewsResult(
            ProductId: productId,
            ProductName: (string?)product?.name ?? "Unknown",
            OverallRating: product is null ? null : (decimal?)product.rating,
            TotalReviews: total,
            Showing: reviews.Count,
            Offset: clampedOffset,
            Reviews: reviews
        );
    }

    // ─────────────────────── analyze_sentiment ───────────────

    [Description("Aggregate sentiment analysis for a product: average rating, rating distribution, and pros/cons summary from review text.")]
    public async Task<SentimentResult> AnalyzeSentiment(
        [Description("UUID of the product to analyze")] string productId
    )
    {
        if (!Guid.TryParse(productId, out var pid))
        {
            return SentimentResult.Failure($"Product not found: {productId}");
        }

        await using var conn = await _pool.OpenAsync();
        var product = await conn.QueryFirstOrDefaultAsync(
            "SELECT name, rating, review_count FROM products WHERE id = @pid",
            new { pid }
        );
        if (product is null)
        {
            return SentimentResult.Failure($"Product not found: {productId}");
        }

        var dist = (await conn.QueryAsync(
            @"SELECT rating, COUNT(*) AS count
              FROM reviews WHERE product_id = @pid
              GROUP BY rating ORDER BY rating DESC",
            new { pid }
        )).ToList();

        var distribution = new Dictionary<string, int>
        {
            ["5"] = 0, ["4"] = 0, ["3"] = 0, ["2"] = 0, ["1"] = 0,
        };
        var totalReviews = 0;
        foreach (var d in dist)
        {
            distribution[((int)d.rating).ToString()] = (int)d.count;
            totalReviews += (int)d.count;
        }

        var verifiedCount = await conn.ExecuteScalarAsync<int>(
            "SELECT COUNT(*) FROM reviews WHERE product_id = @pid AND verified_purchase = TRUE",
            new { pid }
        );
        var verifiedAvg = await conn.ExecuteScalarAsync<decimal?>(
            "SELECT AVG(rating) FROM reviews WHERE product_id = @pid AND verified_purchase = TRUE",
            new { pid }
        );

        // Top-50 by helpful for keyword extraction.
        var reviews = (await conn.QueryAsync(
            @"SELECT rating, title, body FROM reviews
              WHERE product_id = @pid
              ORDER BY helpful_count DESC
              LIMIT 50",
            new { pid }
        )).ToList();

        var prosSet = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var consSet = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var r in reviews)
        {
            var rating = (int)r.rating;
            var text = (((string?)r.title ?? "") + " " + (string)r.body).ToLowerInvariant();
            if (rating >= 4)
            {
                foreach (var kw in PositiveKeywords)
                {
                    if (text.Contains(kw))
                    {
                        prosSet.Add(Capitalise(kw));
                    }
                }
            }
            if (rating <= 2)
            {
                foreach (var kw in NegativeKeywords)
                {
                    if (text.Contains(kw))
                    {
                        consSet.Add(Capitalise(kw));
                    }
                }
            }
        }

        var avg = (decimal)product.rating;
        var sentiment = avg switch
        {
            >= 4.5m => "very_positive",
            >= 3.5m => "positive",
            >= 2.5m => "mixed",
            >= 1.5m => "negative",
            _ => "very_negative",
        };

        return new SentimentResult(
            Error: null,
            ProductId: productId,
            ProductName: (string)product.name,
            OverallSentiment: sentiment,
            AverageRating: avg,
            TotalReviews: totalReviews,
            RatingDistribution: distribution,
            VerifiedReviews: verifiedCount,
            UnverifiedReviews: totalReviews - verifiedCount,
            VerifiedAvgRating: verifiedAvg is null ? null : Math.Round(verifiedAvg.Value, 2),
            Pros: prosSet.Take(5).ToList(),
            Cons: consSet.Take(5).ToList()
        );
    }

    private static string Capitalise(string s) =>
        string.IsNullOrEmpty(s) ? s : char.ToUpperInvariant(s[0]) + s[1..];

    // ─────────────────────── search_reviews ──────────────────

    [Description("Search reviews for a product by keyword in the review title or body.")]
    public async Task<ReviewSearchResult> SearchReviews(
        [Description("UUID of the product")] string productId,
        [Description("Keyword to search in review title and body")] string keyword
    )
    {
        if (!Guid.TryParse(productId, out var pid))
        {
            return ReviewSearchResult.Failure($"Product not found: {productId}");
        }
        if (string.IsNullOrWhiteSpace(keyword))
        {
            return ReviewSearchResult.Failure("keyword is required");
        }

        await using var conn = await _pool.OpenAsync();
        var product = await conn.QueryFirstOrDefaultAsync(
            "SELECT name FROM products WHERE id = @pid",
            new { pid }
        );
        if (product is null)
        {
            return ReviewSearchResult.Failure($"Product not found: {productId}");
        }

        var rowsRaw = (await conn.QueryAsync(
            @"SELECT r.id, r.rating, r.title, r.body, r.verified_purchase,
                     r.helpful_count, r.created_at, u.name AS reviewer_name
              FROM reviews r
              JOIN users u ON r.user_id = u.id
              WHERE r.product_id = @pid
                AND (r.title ILIKE @kw OR r.body ILIKE @kw)
              ORDER BY r.helpful_count DESC, r.created_at DESC
              LIMIT 20",
            new { pid, kw = $"%{keyword}%" }
        )).ToList();

        var reviews = rowsRaw.Select(r => new ReviewEntry(
            Id: ((Guid)r.id).ToString(),
            Rating: (int)r.rating,
            Title: (string?)r.title,
            Body: (string)r.body,
            VerifiedPurchase: (bool)r.verified_purchase,
            HelpfulCount: (int)r.helpful_count,
            IsFlagged: false,
            Reviewer: (string)r.reviewer_name,
            Date: ((DateTime)r.created_at).ToString("o")
        )).ToList();

        return new ReviewSearchResult(
            Error: null,
            ProductId: productId,
            ProductName: (string?)product.name,
            Keyword: keyword,
            Matches: reviews.Count,
            Reviews: reviews
        );
    }

    // ─────────────────────── compare_product_reviews ─────────

    [Description("Compare review metrics (average rating, review count, sentiment) across 2-3 products.")]
    public async Task<ComparisonResult> CompareProductReviews(
        [Description("List of 2-3 product UUIDs to compare")] List<string> productIds
    )
    {
        if (productIds.Count < 2 || productIds.Count > 3)
        {
            return ComparisonResult.Failure("Please provide 2-3 product IDs to compare");
        }

        var comparisons = new List<ProductComparison>();
        await using var conn = await _pool.OpenAsync();
        foreach (var pidStr in productIds)
        {
            if (!Guid.TryParse(pidStr, out var pid))
            {
                comparisons.Add(ProductComparison.Failure(pidStr, "Product not found"));
                continue;
            }

            var product = await conn.QueryFirstOrDefaultAsync(
                "SELECT name, rating, review_count FROM products WHERE id = @pid",
                new { pid }
            );
            if (product is null)
            {
                comparisons.Add(ProductComparison.Failure(pidStr, "Product not found"));
                continue;
            }

            var dist = (await conn.QueryAsync(
                @"SELECT rating, COUNT(*) AS count
                  FROM reviews WHERE product_id = @pid
                  GROUP BY rating ORDER BY rating DESC",
                new { pid }
            )).ToList();
            var distribution = new Dictionary<string, int>
            {
                ["5"] = 0, ["4"] = 0, ["3"] = 0, ["2"] = 0, ["1"] = 0,
            };
            foreach (var d in dist)
            {
                distribution[((int)d.rating).ToString()] = (int)d.count;
            }

            var verified = await conn.ExecuteScalarAsync<int>(
                "SELECT COUNT(*) FROM reviews WHERE product_id = @pid AND verified_purchase = TRUE",
                new { pid }
            );

            var recentAvg = await conn.ExecuteScalarAsync<decimal?>(
                @"SELECT AVG(rating) FROM reviews
                  WHERE product_id = @pid
                    AND created_at >= NOW() - INTERVAL '90 days'",
                new { pid }
            );

            comparisons.Add(new ProductComparison(
                Error: null,
                ProductId: pidStr,
                ProductName: (string)product.name,
                AverageRating: (decimal)product.rating,
                ReviewCount: (int)product.review_count,
                RatingDistribution: distribution,
                VerifiedReviews: verified,
                RecentAvgRating: recentAvg is null ? null : Math.Round(recentAvg.Value, 2)
            ));
        }

        return new ComparisonResult(Error: null, Comparisons: comparisons);
    }
}

// ─────────────────────── DTOs ───────────────────────

public sealed record ReviewEntry(
    string Id,
    int Rating,
    string? Title,
    string Body,
    bool VerifiedPurchase,
    int HelpfulCount,
    bool IsFlagged,
    string Reviewer,
    string Date
);

public sealed record ProductReviewsResult(
    string ProductId,
    string ProductName,
    decimal? OverallRating,
    int TotalReviews,
    int Showing,
    int Offset,
    List<ReviewEntry> Reviews
);

public sealed record SentimentResult(
    string? Error,
    string ProductId,
    string? ProductName,
    string? OverallSentiment,
    decimal? AverageRating,
    int? TotalReviews,
    Dictionary<string, int>? RatingDistribution,
    int? VerifiedReviews,
    int? UnverifiedReviews,
    decimal? VerifiedAvgRating,
    List<string>? Pros,
    List<string>? Cons
)
{
    public static SentimentResult Failure(string error) =>
        new(error, "", null, null, null, null, null, null, null, null, null, null);
}

public sealed record ReviewSearchResult(
    string? Error,
    string ProductId,
    string? ProductName,
    string Keyword,
    int Matches,
    List<ReviewEntry>? Reviews
)
{
    public static ReviewSearchResult Failure(string error) =>
        new(error, "", null, "", 0, null);
}

public sealed record ProductComparison(
    string? Error,
    string ProductId,
    string? ProductName,
    decimal? AverageRating,
    int? ReviewCount,
    Dictionary<string, int>? RatingDistribution,
    int? VerifiedReviews,
    decimal? RecentAvgRating
)
{
    public static ProductComparison Failure(string productId, string error) =>
        new(error, productId, null, null, null, null, null, null);
}

public sealed record ComparisonResult(string? Error, List<ProductComparison>? Comparisons)
{
    public static ComparisonResult Failure(string error) => new(error, null);
}
