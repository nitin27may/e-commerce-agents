using Dapper;
using ECommerceAgents.ReviewSentiment.Tools;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using Xunit;

namespace ECommerceAgents.ReviewSentiment.Tests;

[CollectionDefinition(nameof(LocalPostgresCollection))]
public sealed class LocalPostgresCollection : ICollectionFixture<PostgresFixture> { }

[Collection(nameof(LocalPostgresCollection))]
public sealed class ReviewToolsTests : IAsyncLifetime
{
    private readonly PostgresFixture _pg;
    private DatabasePool _pool = null!;
    private ReviewTools _tools = null!;
    private Guid _productAId;
    private Guid _productBId;

    public ReviewToolsTests(PostgresFixture pg) => _pg = pg;

    public async Task InitializeAsync()
    {
        var settings = new AgentSettings { DatabaseUrl = _pg.ConnectionString };
        _pool = new DatabasePool(settings);
        _tools = new ReviewTools(_pool);
        await SeedAsync();
    }

    public async Task DisposeAsync()
    {
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            "TRUNCATE reviews, products, users RESTART IDENTITY CASCADE"
        );
        await _pool.DisposeAsync();
    }

    // ─────────────────────── get_product_reviews ─────────────

    [Fact]
    public async Task GetProductReviews_PaginatesAndSorts()
    {
        var first = await _tools.GetProductReviews(_productAId.ToString(), sortBy: "newest", limit: 2);
        first.Should().NotBeNull();
        first!.TotalReviews.Should().Be(5);
        first.Showing.Should().Be(2);

        var second = await _tools.GetProductReviews(_productAId.ToString(), sortBy: "newest", limit: 2, offset: 2);
        second!.Showing.Should().Be(2);
        second.Reviews[0].Id.Should().NotBe(first.Reviews[0].Id);
    }

    [Fact]
    public async Task GetProductReviews_HelpfulSortPutsHighestFirst()
    {
        var rows = await _tools.GetProductReviews(_productAId.ToString(), sortBy: "helpful", limit: 5);
        rows!.Reviews[0].HelpfulCount.Should().BeGreaterThanOrEqualTo(rows.Reviews[1].HelpfulCount);
    }

    [Fact]
    public async Task GetProductReviews_RejectsBadGuid()
    {
        var rows = await _tools.GetProductReviews("not-a-uuid");
        rows.Should().BeNull();
    }

    [Fact]
    public async Task GetProductReviews_MaliciousSortByFallsBackToDefault()
    {
        var rows = await _tools.GetProductReviews(
            _productAId.ToString(),
            sortBy: "1; DROP TABLE reviews; --"
        );
        rows.Should().NotBeNull();
        rows!.Showing.Should().BeGreaterThan(0);
    }

    // ─────────────────────── analyze_sentiment ───────────────

    [Fact]
    public async Task AnalyzeSentiment_BuildsDistributionAndExtractsPros()
    {
        var result = await _tools.AnalyzeSentiment(_productAId.ToString());
        result.Error.Should().BeNull();
        result.OverallSentiment.Should().NotBeNull();
        result.RatingDistribution.Should().NotBeNull();
        result.RatingDistribution!["5"].Should().BeGreaterThan(0);
        result.Pros.Should().NotBeNull();
        result.Pros.Should().Contain(p => string.Equals(p, "Excellent", StringComparison.OrdinalIgnoreCase));
    }

    [Fact]
    public async Task AnalyzeSentiment_RejectsUnknownProduct()
    {
        var result = await _tools.AnalyzeSentiment(Guid.NewGuid().ToString());
        result.Error.Should().Contain("not found");
    }

    // ─────────────────────── search_reviews ──────────────────

    [Fact]
    public async Task SearchReviews_FindsKeywordInBody()
    {
        var result = await _tools.SearchReviews(_productAId.ToString(), "excellent");
        result.Error.Should().BeNull();
        result.Matches.Should().BeGreaterThan(0);
        result.Reviews!.Should().OnlyContain(r =>
            (r.Title ?? "").Contains("excellent", StringComparison.OrdinalIgnoreCase)
            || r.Body.Contains("excellent", StringComparison.OrdinalIgnoreCase));
    }

    [Fact]
    public async Task SearchReviews_RejectsEmptyKeyword()
    {
        var result = await _tools.SearchReviews(_productAId.ToString(), "  ");
        result.Error.Should().Contain("keyword");
    }

    [Fact]
    public async Task SearchReviews_KeywordWithSqlMetaIsParameterised()
    {
        var result = await _tools.SearchReviews(_productAId.ToString(), "%; DROP TABLE reviews; --");
        result.Should().NotBeNull();
        await using var conn = await _pool.OpenAsync();
        var count = await conn.ExecuteScalarAsync<int>("SELECT COUNT(*) FROM reviews");
        count.Should().BeGreaterThan(0);
    }

    // ─────────────────────── compare_product_reviews ─────────

    [Fact]
    public async Task CompareProductReviews_RequiresTwoToThreeIds()
    {
        var lone = await _tools.CompareProductReviews([_productAId.ToString()]);
        lone.Error.Should().Contain("2-3");

        var ok = await _tools.CompareProductReviews([_productAId.ToString(), _productBId.ToString()]);
        ok.Error.Should().BeNull();
        ok.Comparisons.Should().HaveCount(2);
    }

    [Fact]
    public async Task CompareProductReviews_ReturnsErrorRowForUnknownProduct()
    {
        var result = await _tools.CompareProductReviews([_productAId.ToString(), Guid.NewGuid().ToString()]);
        result.Comparisons.Should().HaveCount(2);
        result.Comparisons!.Should().Contain(c => c.Error == "Product not found");
    }

    // ─────────────────────── seed ────────────────────────────

    private async Task SeedAsync()
    {
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            "TRUNCATE reviews, products, users RESTART IDENTITY CASCADE"
        );

        var userId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO users (email, password_hash, name, role)
              VALUES ('reviewer@example.com', 'x', 'Reviewer', 'customer')
              RETURNING id"
        );
        _productAId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO products (name, description, category, brand, price, rating, review_count)
              VALUES ('Headphones', 'Sample', 'Electronics', 'X', 200, 4.6, 5)
              RETURNING id"
        );
        _productBId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO products (name, description, category, brand, price, rating, review_count)
              VALUES ('Speaker',    'Sample', 'Electronics', 'Y', 150, 3.0, 2)
              RETURNING id"
        );

        // Five reviews for product A — mix of ratings + keywords for sentiment.
        await conn.ExecuteAsync(
            @"INSERT INTO reviews (product_id, user_id, rating, title, body, verified_purchase, helpful_count, created_at)
              VALUES
                (@a, @u, 5, 'Excellent quality',  'These are amazing and great quality, worth every penny.', TRUE, 10, NOW() - INTERVAL '5 days'),
                (@a, @u, 5, 'Love them',          'Excellent sound, perfect comfort.',                        TRUE, 7,  NOW() - INTERVAL '4 days'),
                (@a, @u, 4, 'Pretty good',        'Comfortable and worth it.',                                TRUE, 3,  NOW() - INTERVAL '3 days'),
                (@a, @u, 2, 'Disappointed',       'Cheap feel, broken after a month.',                        FALSE, 4, NOW() - INTERVAL '2 days'),
                (@a, @u, 1, 'Terrible',           'Defective unit, waste of money.',                          FALSE, 1, NOW() - INTERVAL '1 day')",
            new { a = _productAId, u = userId }
        );

        // Two reviews for product B.
        await conn.ExecuteAsync(
            @"INSERT INTO reviews (product_id, user_id, rating, title, body, verified_purchase, helpful_count)
              VALUES (@b, @u, 3, 'Mid', 'It is okay.', TRUE, 0),
                     (@b, @u, 3, 'Average', 'Not great, not bad.', FALSE, 0)",
            new { b = _productBId, u = userId }
        );
    }
}
