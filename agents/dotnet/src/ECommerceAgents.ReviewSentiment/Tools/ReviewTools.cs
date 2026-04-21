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
        AIFunctionFactory.Create(GetSentimentByTopic, nameof(GetSentimentByTopic)),
        AIFunctionFactory.Create(GetSentimentTrend, nameof(GetSentimentTrend)),
        AIFunctionFactory.Create(DetectFakeReviews, nameof(DetectFakeReviews)),
        AIFunctionFactory.Create(DraftSellerResponse, nameof(DraftSellerResponse)),
    };

    private static readonly IReadOnlyDictionary<string, string[]> TopicKeywords = new Dictionary<string, string[]>
    {
        ["quality"] = ["quality", "well-made", "well made", "craftsmanship", "build", "material", "sturdy", "solid", "premium"],
        ["value"] = ["value", "price", "worth", "money", "expensive", "cheap", "affordable", "overpriced", "bargain", "deal"],
        ["shipping"] = ["shipping", "delivery", "arrived", "package", "packaging", "shipped", "transit", "late", "fast delivery"],
        ["design"] = ["design", "look", "style", "aesthetic", "beautiful", "color", "colour", "sleek", "modern", "appearance"],
        ["durability"] = ["durable", "durability", "lasting", "broke", "broken", "wear", "tear", "fragile", "robust", "lifespan"],
    };

    private static readonly string[] GenericFakePatterns =
    [
        "great product", "highly recommend", "five stars", "5 stars",
        "best product ever", "love it", "amazing product", "perfect product",
        "must buy", "worth every penny",
    ];

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

    // ─────────────────────── get_sentiment_by_topic ──────────

    [Description("Break down reviews into topics (quality, value, shipping, design, durability) with mention counts and average rating per topic.")]
    public async Task<TopicSentimentResult> GetSentimentByTopic(
        [Description("UUID of the product")] string productId
    )
    {
        if (!Guid.TryParse(productId, out var pid))
        {
            return TopicSentimentResult.Failure($"Product not found: {productId}");
        }

        await using var conn = await _pool.OpenAsync();
        var product = await conn.QueryFirstOrDefaultAsync(
            "SELECT name FROM products WHERE id = @pid",
            new { pid }
        );
        if (product is null)
        {
            return TopicSentimentResult.Failure($"Product not found: {productId}");
        }

        var reviews = (await conn.QueryAsync(
            "SELECT rating, title, body FROM reviews WHERE product_id = @pid",
            new { pid }
        )).Select(r => new
        {
            Rating = (int)r.rating,
            Text = ((string?)r.title ?? "" + " " + (string)r.body).ToLowerInvariant(),
        }).ToList();

        var topics = new Dictionary<string, TopicSentiment>();
        foreach (var (topic, keywords) in TopicKeywords)
        {
            var mentions = 0;
            var ratings = new List<int>();
            foreach (var r in reviews)
            {
                if (keywords.Any(kw => r.Text.Contains(kw)))
                {
                    mentions++;
                    ratings.Add(r.Rating);
                }
            }

            decimal? avg = ratings.Count == 0
                ? null
                : Math.Round((decimal)ratings.Average(), 2);
            var sentiment = avg switch
            {
                null => "no_data",
                >= 3.5m => "positive",
                < 2.5m => "negative",
                _ => "mixed",
            };
            topics[topic] = new TopicSentiment(mentions, avg, sentiment);
        }

        return new TopicSentimentResult(
            Error: null,
            ProductId: productId,
            ProductName: (string)product.name,
            TotalReviewsAnalyzed: reviews.Count,
            Topics: topics
        );
    }

    // ─────────────────────── get_sentiment_trend ─────────────

    [Description("Track sentiment over time with monthly average ratings for a product.")]
    public async Task<SentimentTrendResult> GetSentimentTrend(
        [Description("UUID of the product")] string productId,
        [Description("Number of months to look back (1-36)")] int months = 6
    )
    {
        if (!Guid.TryParse(productId, out var pid))
        {
            return SentimentTrendResult.Failure($"Product not found: {productId}");
        }
        months = Math.Clamp(months, 1, 36);

        await using var conn = await _pool.OpenAsync();
        var product = await conn.QueryFirstOrDefaultAsync(
            "SELECT name FROM products WHERE id = @pid",
            new { pid }
        );
        if (product is null)
        {
            return SentimentTrendResult.Failure($"Product not found: {productId}");
        }

        var rows = (await conn.QueryAsync(
            @"SELECT DATE_TRUNC('month', created_at) AS month,
                     AVG(rating) AS avg_rating,
                     COUNT(*) AS review_count
              FROM reviews
              WHERE product_id = @pid
                AND created_at >= NOW() - (@months || ' months')::interval
              GROUP BY DATE_TRUNC('month', created_at)
              ORDER BY month ASC",
            new { pid, months = months.ToString() }
        )).Select(r => new MonthlySentiment(
            Month: ((DateTime)r.month).ToString("yyyy-MM"),
            AverageRating: Math.Round((decimal)r.avg_rating, 2),
            ReviewCount: (int)r.review_count
        )).ToList();

        string trend;
        if (rows.Count < 2)
        {
            trend = "insufficient_data";
        }
        else
        {
            var mid = rows.Count / 2;
            var firstHalf = rows.Take(mid).ToList();
            var secondHalf = rows.Skip(mid).ToList();
            var firstAvg = firstHalf.Average(t => t.AverageRating);
            var secondAvg = secondHalf.Average(t => t.AverageRating);
            trend = secondAvg > firstAvg + 0.2m
                ? "improving"
                : secondAvg < firstAvg - 0.2m
                    ? "declining"
                    : "stable";
        }

        return new SentimentTrendResult(
            Error: null,
            ProductId: productId,
            ProductName: (string)product.name,
            PeriodMonths: months,
            Trend: trend,
            MonthlyData: rows
        );
    }

    // ─────────────────────── detect_fake_reviews ─────────────

    [Description("Detect potentially fake or suspicious reviews for a product. Checks flagged reviews, unverified 5-star ratings, and generic language patterns.")]
    public async Task<FakeReviewResult> DetectFakeReviews(
        [Description("UUID of the product to check")] string productId
    )
    {
        if (!Guid.TryParse(productId, out var pid))
        {
            return FakeReviewResult.Failure($"Product not found: {productId}");
        }

        await using var conn = await _pool.OpenAsync();
        var product = await conn.QueryFirstOrDefaultAsync(
            "SELECT name FROM products WHERE id = @pid",
            new { pid }
        );
        if (product is null)
        {
            return FakeReviewResult.Failure($"Product not found: {productId}");
        }

        var total = await conn.ExecuteScalarAsync<int>(
            "SELECT COUNT(*) FROM reviews WHERE product_id = @pid",
            new { pid }
        );

        var flagged = (await conn.QueryAsync(
            @"SELECT r.id, r.rating, r.title, r.body, r.verified_purchase,
                     r.created_at, u.name AS reviewer_name
              FROM reviews r
              JOIN users u ON r.user_id = u.id
              WHERE r.product_id = @pid AND r.is_flagged = TRUE
              ORDER BY r.created_at DESC",
            new { pid }
        )).Select(r => new SuspiciousReview(
            ReviewId: ((Guid)r.id).ToString(),
            Rating: (int)r.rating,
            Title: (string?)r.title,
            BodyPreview: Truncate((string)r.body, 100),
            VerifiedPurchase: (bool)r.verified_purchase,
            MatchedPatterns: null,
            Reason: "Previously flagged as suspicious",
            Reviewer: (string)r.reviewer_name,
            Date: ((DateTime)r.created_at).ToString("o")
        )).ToList();

        var unverifiedFive = (await conn.QueryAsync(
            @"SELECT r.id, r.rating, r.title, r.body, r.created_at,
                     u.name AS reviewer_name
              FROM reviews r
              JOIN users u ON r.user_id = u.id
              WHERE r.product_id = @pid
                AND r.verified_purchase = FALSE
                AND r.rating = 5
                AND r.is_flagged = FALSE
              ORDER BY r.created_at DESC
              LIMIT 20",
            new { pid }
        )).Select(r => new SuspiciousReview(
            ReviewId: ((Guid)r.id).ToString(),
            Rating: (int)r.rating,
            Title: (string?)r.title,
            BodyPreview: Truncate((string)r.body, 100),
            VerifiedPurchase: false,
            MatchedPatterns: null,
            Reason: "Unverified purchase with 5-star rating",
            Reviewer: (string)r.reviewer_name,
            Date: ((DateTime)r.created_at).ToString("o")
        )).ToList();

        var allRows = (await conn.QueryAsync(
            @"SELECT r.id, r.rating, r.title, r.body, r.verified_purchase,
                     r.created_at, u.name AS reviewer_name
              FROM reviews r
              JOIN users u ON r.user_id = u.id
              WHERE r.product_id = @pid AND r.is_flagged = FALSE
              ORDER BY r.created_at DESC",
            new { pid }
        )).ToList();

        var generic = new List<SuspiciousReview>();
        foreach (var r in allRows)
        {
            var body = (string)r.body;
            var text = (((string?)r.title ?? "") + " " + body).ToLowerInvariant();
            var matched = GenericFakePatterns.Where(p => text.Contains(p)).ToList();
            if (matched.Count > 0 && body.Length < 100 && (int)r.rating >= 4)
            {
                generic.Add(new SuspiciousReview(
                    ReviewId: ((Guid)r.id).ToString(),
                    Rating: (int)r.rating,
                    Title: (string?)r.title,
                    BodyPreview: Truncate(body, 100),
                    VerifiedPurchase: (bool)r.verified_purchase,
                    MatchedPatterns: matched,
                    Reason: "Short review with generic language",
                    Reviewer: (string)r.reviewer_name,
                    Date: ((DateTime)r.created_at).ToString("o")
                ));
            }
        }

        var suspiciousCount = flagged.Count + unverifiedFive.Count + generic.Count;
        string risk;
        if (total == 0)
        {
            risk = "low";
        }
        else if (suspiciousCount > total * 0.3)
        {
            risk = "high";
        }
        else if (suspiciousCount > total * 0.1)
        {
            risk = "medium";
        }
        else
        {
            risk = "low";
        }

        return new FakeReviewResult(
            Error: null,
            ProductId: productId,
            ProductName: (string)product.name,
            TotalReviews: total,
            SuspiciousCount: suspiciousCount,
            RiskLevel: risk,
            FlaggedReviews: flagged,
            UnverifiedFiveStar: unverifiedFive,
            GenericLanguageMatches: generic.Take(10).ToList()
        );
    }

    // ─────────────────────── draft_seller_response ───────────

    [Description("Generate a professional response template for a review. Returns a template the seller can customize.")]
    public async Task<SellerResponseResult> DraftSellerResponse(
        [Description("UUID of the review to respond to")] string reviewId
    )
    {
        if (!Guid.TryParse(reviewId, out var rid))
        {
            return SellerResponseResult.Failure($"Review not found: {reviewId}");
        }

        await using var conn = await _pool.OpenAsync();
        var row = await conn.QueryFirstOrDefaultAsync(
            @"SELECT r.id, r.rating, r.title, r.body, r.created_at,
                     u.name AS reviewer_name, p.name AS product_name
              FROM reviews r
              JOIN users u ON r.user_id = u.id
              JOIN products p ON r.product_id = p.id
              WHERE r.id = @id",
            new { id = rid }
        );
        if (row is null)
        {
            return SellerResponseResult.Failure($"Review not found: {reviewId}");
        }

        var reviewer = (string)row.reviewer_name;
        var productName = (string)row.product_name;
        var rating = (int)row.rating;

        var template = rating <= 2
            ? $"Dear {reviewer},\n\nThank you for taking the time to share your feedback about the {productName}. We sincerely apologize that your experience did not meet your expectations.\n\nWe take all feedback seriously and would like the opportunity to make this right. Could you please reach out to our customer support team so we can investigate your concern and find a suitable resolution?\n\nWe appreciate your patience and look forward to resolving this for you.\n\nBest regards,\n[Your Name]\nSeller Support Team"
            : rating == 3
                ? $"Dear {reviewer},\n\nThank you for your honest review of the {productName}. We appreciate you highlighting both the positives and areas for improvement.\n\nYour feedback helps us enhance our products and service. If there is anything specific we can do to improve your experience, please do not hesitate to contact our support team.\n\nThank you for choosing our platform.\n\nBest regards,\n[Your Name]\nSeller Support Team"
                : $"Dear {reviewer},\n\nThank you for your review of the {productName}! We are glad to hear about your experience.\n\nIf there is anything else we can help with, please let us know.\n\nBest regards,\n[Your Name]\nSeller Support Team";

        var body = (string)row.body;
        return new SellerResponseResult(
            Error: null,
            ReviewId: ((Guid)row.id).ToString(),
            ProductName: productName,
            Reviewer: reviewer,
            Rating: rating,
            ReviewTitle: (string?)row.title,
            ReviewBody: Truncate(body, 200),
            ResponseTemplate: template,
            Note: "This is a template. Customize it with specific details about the customer's concern before sending."
        );
    }

    private static string Truncate(string value, int max) =>
        value.Length <= max ? value : value[..max];
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

public sealed record TopicSentiment(int Mentions, decimal? AverageRating, string Sentiment);

public sealed record TopicSentimentResult(
    string? Error,
    string ProductId,
    string? ProductName,
    int? TotalReviewsAnalyzed,
    Dictionary<string, TopicSentiment>? Topics
)
{
    public static TopicSentimentResult Failure(string error) => new(error, "", null, null, null);
}

public sealed record MonthlySentiment(string Month, decimal AverageRating, int ReviewCount);

public sealed record SentimentTrendResult(
    string? Error,
    string ProductId,
    string? ProductName,
    int? PeriodMonths,
    string? Trend,
    List<MonthlySentiment>? MonthlyData
)
{
    public static SentimentTrendResult Failure(string error) => new(error, "", null, null, null, null);
}

public sealed record SuspiciousReview(
    string ReviewId,
    int Rating,
    string? Title,
    string BodyPreview,
    bool VerifiedPurchase,
    List<string>? MatchedPatterns,
    string Reason,
    string Reviewer,
    string Date
);

public sealed record FakeReviewResult(
    string? Error,
    string ProductId,
    string? ProductName,
    int? TotalReviews,
    int? SuspiciousCount,
    string? RiskLevel,
    List<SuspiciousReview>? FlaggedReviews,
    List<SuspiciousReview>? UnverifiedFiveStar,
    List<SuspiciousReview>? GenericLanguageMatches
)
{
    public static FakeReviewResult Failure(string error) =>
        new(error, "", null, null, null, null, null, null, null);
}

public sealed record SellerResponseResult(
    string? Error,
    string ReviewId,
    string? ProductName,
    string? Reviewer,
    int? Rating,
    string? ReviewTitle,
    string? ReviewBody,
    string? ResponseTemplate,
    string? Note
)
{
    public static SellerResponseResult Failure(string error) =>
        new(error, "", null, null, null, null, null, null, null);
}
