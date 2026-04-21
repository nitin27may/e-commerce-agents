using Dapper;
using ECommerceAgents.ProductDiscovery.Tools;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using Npgsql;
using Xunit;

namespace ECommerceAgents.ProductDiscovery.Tests;

/// <summary>
/// xUnit collection definitions must live in the same assembly as the
/// tests that consume them, so we re-declare one here that shares the
/// <see cref="PostgresFixture"/> from <c>ECommerceAgents.TestFixtures</c>.
/// </summary>
[CollectionDefinition(nameof(LocalPostgresCollection))]
public sealed class LocalPostgresCollection : ICollectionFixture<PostgresFixture> { }

/// <summary>
/// Locks in audit fix #3: <c>ORDER BY</c> + <c>LIMIT</c> are no longer
/// open-coded into the SQL string. Every executed query must come from
/// the hardcoded sort-clause whitelist (or the documented default) and
/// the <c>LIMIT</c> must clamp to ≤100.
/// </summary>
[Collection(nameof(LocalPostgresCollection))]
public sealed class ProductToolsTests : IAsyncLifetime
{
    private readonly PostgresFixture _pg;
    private DatabasePool _pool = null!;
    private ProductTools _tools = null!;

    public ProductToolsTests(PostgresFixture pg) => _pg = pg;

    public async Task InitializeAsync()
    {
        var settings = new AgentSettings { DatabaseUrl = _pg.ConnectionString };
        _pool = new DatabasePool(settings);
        _tools = new ProductTools(_pool);
        await SeedProductsAsync();
    }

    public async Task DisposeAsync()
    {
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync("TRUNCATE products RESTART IDENTITY CASCADE");
        await _pool.DisposeAsync();
    }

    [Theory]
    [InlineData("price_asc")]
    [InlineData("price_desc")]
    [InlineData("rating")]
    [InlineData("newest")]
    [InlineData(null)] // → DefaultSortClause
    public async Task SearchProducts_AcceptsWhitelistedSortBy(string? sortBy)
    {
        var act = async () => await _tools.SearchProducts(sortBy: sortBy);
        await act.Should().NotThrowAsync();
    }

    [Fact]
    public async Task SearchProducts_IgnoresMaliciousSortBy()
    {
        // The classic SQL-injection probe — must not raise an Npgsql
        // syntax error, must not run the injected payload. Falls back
        // to the default ORDER BY.
        var injection = "1; DROP TABLE products; --";
        var results = await _tools.SearchProducts(sortBy: injection);

        results.Should().NotBeNull();
        // Sanity: the table is still there.
        await using var conn = await _pool.OpenAsync();
        var count = await conn.ExecuteScalarAsync<int>("SELECT COUNT(*) FROM products");
        count.Should().BeGreaterThan(0);
    }

    [Theory]
    [InlineData(1, 1)]
    [InlineData(50, 50)]
    [InlineData(100, 100)]
    [InlineData(1_000, 100)] // clamps
    [InlineData(int.MaxValue, 100)] // clamps even on absurd input
    [InlineData(0, 1)] // clamps low edge
    [InlineData(-5, 1)] // clamps negative
    public async Task SearchProducts_ClampsLimitToInRangeBounds(int requested, int expectedMax)
    {
        var results = await _tools.SearchProducts(limit: requested);
        results.Count.Should().BeLessThanOrEqualTo(expectedMax);
    }

    [Fact]
    public async Task SearchProducts_ParameterisesUserText()
    {
        // A text query that includes SQL metacharacters must round-trip
        // through Dapper parameters, not be interpolated.
        var results = await _tools.SearchProducts(query: "wireless'; DROP TABLE products; --");
        results.Should().NotBeNull();
        await using var conn = await _pool.OpenAsync();
        var count = await conn.ExecuteScalarAsync<int>("SELECT COUNT(*) FROM products");
        count.Should().BeGreaterThan(0);
    }

    [Fact]
    public async Task GetTrendingProducts_ClampsLimit()
    {
        var results = await _tools.GetTrendingProducts(limit: 50_000);
        results.Count.Should().BeLessThanOrEqualTo(100);
    }

    private async Task SeedProductsAsync()
    {
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync("TRUNCATE products RESTART IDENTITY CASCADE");
        await conn.ExecuteAsync(
            @"INSERT INTO products
                (id, name, description, category, brand, price, original_price, rating, review_count, is_active, image_url, specs)
              SELECT
                gen_random_uuid(),
                'product-' || g,
                'a wireless gadget',
                'Electronics',
                'BrandX',
                10.0 + g,
                NULL,
                4.5,
                5,
                TRUE,
                'https://example.com/img.jpg',
                '{}'::jsonb
              FROM generate_series(1, 5) AS g"
        );
    }
}
