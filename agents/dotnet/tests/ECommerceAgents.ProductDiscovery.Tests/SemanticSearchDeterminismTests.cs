using System.Globalization;
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
/// Ranking twin of Python's tests/test_seed_determinism.py (#161): the vector arm must rank and display
/// the same on every CPU. pgvector's float32 distance differs in the last digits between NEON and AVX, so
/// similarity is rounded to 6 places in SQL before ranking, ties fall to rating then name, and the
/// displayed value is a half-up round on the decimal.
/// </summary>
[Collection(nameof(LocalPostgresCollection))]
public sealed class SemanticSearchDeterminismTests(PostgresFixture pg) : IAsyncLifetime
{
    private DatabasePool _pool = null!;
    private ProductTools _tools = null!;

    public async Task InitializeAsync()
    {
        var settings = new AgentSettings { DatabaseUrl = pg.ConnectionString, OpenAiApiKey = "test-key" };
        _pool = new DatabasePool(settings);
        _tools = new ProductTools(_pool, new HotVectorProvider());
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
    public async Task VectorsCloserThanFloat32Noise_RankByRatingThenName()
    {
        var baseVec = UnitVector(0.3);
        var nudged = (double[])baseVec.Clone();
        nudged[0] += 3e-8;
        var low = await InsertAsync("Alpha Low Rated", 4.0m, nudged);
        var high = await InsertAsync("Zulu High Rated", 4.9m, baseVec);

        for (var i = 0; i < 3; i++)
        {
            var rows = await _tools.SemanticSearch("anything", limit: 2);

            rows.Select(r => r.Id).Should().Equal(high, low);
            rows[0].Similarity.Should().Be(0.3);
            rows[1].Similarity.Should().Be(0.3);
        }
    }

    [Fact]
    public async Task EqualRatingTies_FallToName()
    {
        var vec = UnitVector(0.4);
        var b = await InsertAsync("Bravo", 4.5m, vec);
        var a = await InsertAsync("Alpha", 4.5m, vec);

        var rows = await _tools.SemanticSearch("anything", limit: 2);

        rows.Select(r => r.Id).Should().Equal(a, b);
    }

    [Fact]
    public async Task Similarity_RoundsHalfUpOnTheDecimal()
    {
        // 0.0625 is exact in binary, where a double Math.Round goes half-even to 0.062.
        await InsertAsync("Exactly One Sixteenth", 4.5m, UnitVector(0.0625));

        var rows = await _tools.SemanticSearch("anything", limit: 1);

        rows[0].Similarity.Should().Be(0.063);
    }

    private async Task<string> InsertAsync(string name, decimal rating, double[] vector)
    {
        await using var conn = await _pool.OpenAsync();
        var id = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO products (name, description, category, brand, price, rating, review_count, is_active)
              VALUES (@name, 'd', 'Books', 'b', 10, @rating, 1, TRUE) RETURNING id",
            new { name, rating });
        await conn.ExecuteAsync(
            "INSERT INTO product_embeddings (product_id, embedding) VALUES (@id, @vec::vector)",
            new { id, vec = Literal(vector) });
        return id.ToString();
    }

    /// <summary>A unit vector whose cosine with the one-hot query e0 is <paramref name="cosine"/>.</summary>
    private static double[] UnitVector(double cosine)
    {
        var v = new double[1536];
        v[0] = cosine;
        v[1] = Math.Sqrt(1.0 - cosine * cosine);
        return v;
    }

    private static string Literal(double[] v) =>
        "[" + string.Join(",", v.Select(x => x.ToString("R", CultureInfo.InvariantCulture))) + "]";

    private sealed class HotVectorProvider : IEmbeddingProvider
    {
        public Task<string> EmbedAsVectorLiteralAsync(string text, CancellationToken ct = default)
        {
            var v = new double[1536];
            v[0] = 1.0;
            return Task.FromResult(Literal(v));
        }
    }
}
