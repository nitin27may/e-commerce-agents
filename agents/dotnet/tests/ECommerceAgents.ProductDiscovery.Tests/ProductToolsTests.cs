using Dapper;
using ECommerceAgents.ProductDiscovery.Tools;
using ECommerceAgents.Shared.Agents;
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
        var settings = new AgentSettings { DatabaseUrl = _pg.ConnectionString, OpenAiApiKey = "test-key" };
        _pool = new DatabasePool(settings);
        _tools = new ProductTools(_pool, EmbeddingClientFactory.CreateProvider(settings));
        await SeedProductsAsync();

        // ivfflat is an approximate-nearest-neighbor index; with only a handful of
        // rows and the schema's `lists = 10`, the default probes=1 can skip most
        // clusters entirely and miss real matches. Raising it database-wide (so it
        // applies regardless of which pooled connection FindSimilarProducts/
        // SemanticSearch happen to get) makes the embedding-ordering tests below
        // deterministic — a tuning knob, not a correctness fix for production,
        // where the catalog is large enough for the default to behave normally.
        await using (var conn = await _pool.OpenAsync())
        {
            await conn.ExecuteAsync("ALTER DATABASE ecommerce_test SET ivfflat.probes = 10");
        }
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

    // ─────────────────────── SearchProducts (full-text) ──────
    // Mirrors Python's tests/test_product_search.py so both stacks are pinned
    // to identical behavior. SearchProducts needs no embedding API call.
    //
    // These replaced a per-word ILIKE-AND matcher that failed two ways: it did
    // no stemming ("noise cancellation" never substring-matched "noise
    // cancelling"), and one absent term emptied the whole result set. Ordering
    // was rating-only, so match quality never influenced the ranking.

    [Fact]
    public async Task SearchProducts_MatchesStemmedTerms()
    {
        var (anc, exact, _, _) = await SeedFtsCatalogAsync();

        var results = await _tools.SearchProducts(query: "noise cancellation headphones");

        var ids = results.Select(r => r.Id).ToList();
        ids.Should().Contain(anc.ToString());
        ids.Should().Contain(exact.ToString());
    }

    [Fact]
    public async Task SearchProducts_DoesNotRequireEveryTerm()
    {
        var (anc, _, _, _) = await SeedFtsCatalogAsync();

        // No product in the catalog mentions bluetooth.
        var results = await _tools.SearchProducts(query: "wireless bluetooth headphones");

        results.Select(r => r.Id).Should().Contain(anc.ToString());
    }

    [Fact]
    public async Task SearchProducts_RanksRelevanceAboveRating()
    {
        var (anc, exact, decoy, _) = await SeedFtsCatalogAsync();

        var results = await _tools.SearchProducts(query: "noise cancellation headphones");

        var ids = results.Select(r => r.Id).ToList();
        ids.Should().Contain(decoy.ToString(), "the decoy still matches on 'headphones'");
        ids.IndexOf(decoy.ToString()).Should().BeGreaterThan(ids.IndexOf(anc.ToString()));
        ids.IndexOf(decoy.ToString()).Should().BeGreaterThan(ids.IndexOf(exact.ToString()));
    }

    [Fact]
    public async Task SearchProducts_WeightsNameAboveDescription()
    {
        var (anc, exact, _, _) = await SeedFtsCatalogAsync();

        var results = await _tools.SearchProducts(query: "noise cancellation headphones");

        var ids = results.Select(r => r.Id).ToList();
        ids.IndexOf(exact.ToString()).Should().BeLessThan(ids.IndexOf(anc.ToString()));
    }

    [Fact]
    public async Task SearchProducts_ComposesFiltersWithQuery()
    {
        var (anc, exact, _, kettle) = await SeedFtsCatalogAsync();

        var results = await _tools.SearchProducts(
            query: "headphones", category: "Electronics", maxPrice: 300m);

        var ids = results.Select(r => r.Id).ToList();
        ids.Should().Contain(anc.ToString());
        ids.Should().NotContain(exact.ToString());   // 349.99 is over the cap
        ids.Should().NotContain(kettle.ToString());  // wrong category
    }

    [Fact]
    public async Task SearchProducts_StopwordOnlyQueryFallsBackToFilters()
    {
        // plainto_tsquery('the ???') is an empty tsquery matching no rows —
        // that must not turn a filtered browse into zero results.
        var (_, _, _, kettle) = await SeedFtsCatalogAsync();

        var results = await _tools.SearchProducts(query: "the ???", category: "Home");

        results.Select(r => r.Id).Should().Equal(kettle.ToString());
    }

    [Fact]
    public async Task SearchProducts_ExplicitSortOverridesRelevance()
    {
        await SeedFtsCatalogAsync();

        var results = await _tools.SearchProducts(query: "headphones", sortBy: "price_asc");

        results.Select(r => r.Price).Should().BeInAscendingOrder();
    }

    /// <summary>
    /// Catalog whose terms differ morphologically from the test queries: no row
    /// anywhere contains the literal substring "cancellation" or "bluetooth".
    /// The decoy is rated highest so relevance and rating disagree.
    /// </summary>
    private async Task<(Guid Anc, Guid Exact, Guid Decoy, Guid Kettle)> SeedFtsCatalogAsync()
    {
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync("TRUNCATE products RESTART IDENTITY CASCADE");

        async Task<Guid> InsertAsync(
            string name, string description, string category, decimal price, decimal rating) =>
            await conn.ExecuteScalarAsync<Guid>(
                @"INSERT INTO products
                    (name, description, category, brand, price, rating, review_count, is_active)
                  VALUES (@name, @description, @category, 'BrandX', @price, @rating, 10, TRUE)
                  RETURNING id",
                new { name, description, category, price, rating }
            );

        var anc = await InsertAsync(
            "Wireless Headphones with ANC",
            "Over-ear wireless headphones with active noise cancelling for travel.",
            "Electronics", 279.99m, 4.5m);
        var exact = await InsertAsync(
            "Noise Cancelling Headphones Pro",
            "Studio-grade over-ear headphones.",
            "Electronics", 349.99m, 4.1m);
        var decoy = await InsertAsync(
            "Phone Case",
            "Slim protective case. Works fine with headphones plugged in.",
            "Electronics", 12.99m, 5.0m);
        var kettle = await InsertAsync(
            "Electric Kettle",
            "1.7L stainless steel kettle with rapid boil.",
            "Home", 59.99m, 4.8m);

        return (anc, exact, decoy, kettle);
    }

    // ─────────────────────── FindSimilarProducts ─────────────
    // No live embedding API call needed — the tool only reads pre-stored
    // pgvector rows, so tests seed product_embeddings directly with
    // literal vectors rather than mocking OpenAI.Embeddings.EmbeddingClient.

    [Fact]
    public async Task FindSimilarProducts_ReturnsErrorWhenNoEmbeddingExists()
    {
        var result = await _tools.FindSimilarProducts(Guid.NewGuid().ToString());

        result.Should().ContainSingle();
        result[0].Error.Should().NotBeNullOrEmpty();
        result[0].Id.Should().BeNull();
    }

    [Fact]
    public async Task FindSimilarProducts_OrdersByCosineSimilarityAndExcludesSelf()
    {
        var (refId, closeId, farId) = await SeedEmbeddingsAsync();

        var results = await _tools.FindSimilarProducts(refId.ToString(), limit: 5);

        results.Select(r => r.Id).Should().NotContain(refId.ToString());
        results.Select(r => r.Id).Should().ContainInOrder(closeId.ToString(), farId.ToString());
        results.Should().AllSatisfy(r => r.Error.Should().BeNull());
        results.First(r => r.Id == closeId.ToString()).Similarity.Should().BeApproximately(1.0, 0.0001);
        results.First(r => r.Id == farId.ToString()).Similarity.Should().BeApproximately(0.0, 0.0001);
    }

    /// <summary>Seeds 3 products with hand-built 1536-dim pgvector rows: <c>refId</c>
    /// and <c>closeId</c> get an identical "hot at index 0" vector (cosine
    /// similarity 1.0), <c>farId</c> gets an orthogonal "hot at index 1" vector
    /// (cosine similarity 0.0) — a deterministic ordering with no API call.</summary>
    private async Task<(Guid RefId, Guid CloseId, Guid FarId)> SeedEmbeddingsAsync()
    {
        await using var conn = await _pool.OpenAsync();

        async Task<Guid> InsertProductAsync(string name) =>
            await conn.ExecuteScalarAsync<Guid>(
                @"INSERT INTO products
                    (name, description, category, brand, price, rating, review_count, is_active)
                  VALUES (@name, 'embedding test product', 'Electronics', 'BrandX', 50.0, 4.5, 5, TRUE)
                  RETURNING id",
                new { name }
            );

        var refId = await InsertProductAsync("embed-ref");
        var closeId = await InsertProductAsync("embed-close");
        var farId = await InsertProductAsync("embed-far");

        static string HotVector(int hotIndex)
        {
            var values = new double[1536];
            values[hotIndex] = 1.0;
            return "[" + string.Join(",", values) + "]";
        }

        await conn.ExecuteAsync(
            "INSERT INTO product_embeddings (product_id, embedding) VALUES (@pid, @vec::vector)",
            new[]
            {
                new { pid = refId, vec = HotVector(0) },
                new { pid = closeId, vec = HotVector(0) },
                new { pid = farId, vec = HotVector(1) },
            }
        );

        return (refId, closeId, farId);
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
