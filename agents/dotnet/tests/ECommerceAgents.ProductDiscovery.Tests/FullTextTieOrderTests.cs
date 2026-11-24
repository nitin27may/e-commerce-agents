using Dapper;
using ECommerceAgents.ProductDiscovery.Tools;
using ECommerceAgents.Shared.Agents;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using Xunit;

namespace ECommerceAgents.ProductDiscovery.Tests;

/// <summary>
/// Twin of Python's tied-full-text tests in tests/test_product_search.py: products with equal ts_rank, rating and
/// review count come back ordered by id, in the list search and in the text arm of semantic search, whatever order
/// the rows were inserted in.
/// </summary>
[Collection(nameof(LocalPostgresCollection))]
public sealed class FullTextTieOrderTests(PostgresFixture pg) : IAsyncLifetime
{
    private DatabasePool _pool = null!;
    private ProductTools _tools = null!;

    public async Task InitializeAsync()
    {
        var settings = new AgentSettings { DatabaseUrl = pg.ConnectionString, OpenAiApiKey = "test-key" };
        _pool = new DatabasePool(settings);
        _tools = new ProductTools(_pool, new NoEmbeddings());
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync("TRUNCATE products RESTART IDENTITY CASCADE");
    }

    public async Task DisposeAsync()
    {
        await using (var conn = await _pool.OpenAsync())
        {
            await conn.ExecuteAsync("TRUNCATE products RESTART IDENTITY CASCADE");
        }

        await _pool.DisposeAsync();
    }

    [Fact]
    public async Task SearchProducts_TiedMatches_ComeBackInIdOrder()
    {
        var expected = await InsertTwinsAsync();

        var rows = await _tools.SearchProducts(query: "camping lantern");

        rows.Select(r => r.Id).Should().Equal(expected);
    }

    [Fact]
    public async Task SemanticSearch_TiedTextArm_RanksById()
    {
        var expected = await InsertTwinsAsync();

        var rows = await _tools.SemanticSearch("camping lantern", limit: 5);

        rows.Select(r => r.Id).Should().Equal(expected);
    }

    private async Task<List<string>> InsertTwinsAsync()
    {
        var ids = new[] { 9, 7, 5, 3, 1 }.Select(n => Guid.Parse($"{n}0000000-0000-4000-8000-000000000000")).ToList();
        await using var conn = await _pool.OpenAsync();
        foreach (var id in ids)
        {
            await conn.ExecuteAsync(
                @"INSERT INTO products (id, name, description, category, brand, price, rating, review_count, is_active)
                  VALUES (@id, 'Twin Lantern', 'Camping lantern', 'Outdoor', 'Twin', 20, 4.0, 10, TRUE)",
                new { id });
        }

        return ids.Select(i => i.ToString()).OrderBy(s => s, StringComparer.Ordinal).ToList();
    }

    /// <summary>The embedding call fails, so semantic search runs its full-text arm alone.</summary>
    private sealed class NoEmbeddings : IEmbeddingProvider
    {
        public Task<string> EmbedAsVectorLiteralAsync(string text, CancellationToken ct = default) =>
            throw new InvalidOperationException("no embedding provider in this test");
    }
}
