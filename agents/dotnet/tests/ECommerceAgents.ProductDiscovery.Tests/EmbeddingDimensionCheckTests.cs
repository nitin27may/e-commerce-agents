using ECommerceAgents.Shared.Agents;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using Xunit;

namespace ECommerceAgents.ProductDiscovery.Tests;

/// <summary>
/// The startup check against a real pgvector database: the baseline's 1536-wide columns agree with the default
/// <c>EMBEDDING_DIM</c> and disagree, naming the fix, with any other.
/// </summary>
[Collection(nameof(LocalPostgresCollection))]
public sealed class EmbeddingDimensionCheckTests(PostgresFixture pg)
{
    [Fact]
    public async Task Passes_WhenEmbeddingDimMatchesTheColumns()
    {
        await using var pool = new DatabasePool(new AgentSettings { DatabaseUrl = pg.ConnectionString });
        await using var conn = await pool.OpenAsync();

        var dims = await EmbeddingDimensionCheck.ColumnDimensionsAsync(conn);
        dims.Should().Contain(new KeyValuePair<string, int?>("product_embeddings", 1536));
        dims.Should().Contain(new KeyValuePair<string, int?>("agent_memories", 1536));

        await EmbeddingDimensionCheck.VerifyAsync(conn, new AgentSettings());
    }

    [Fact]
    public async Task Fails_NamingTheFix_WhenItDoesNot()
    {
        await using var pool = new DatabasePool(new AgentSettings { DatabaseUrl = pg.ConnectionString });

        var act = () => EmbeddingDimensionCheck.VerifyAsync(pool, new AgentSettings { EmbeddingDim = 768 });

        var ex = (await act.Should().ThrowAsync<InvalidOperationException>()).Which;
        ex.Message.Should().Contain("EMBEDDING_DIM=768")
            .And.Contain("product_embeddings.embedding is vector(1536)")
            .And.Contain("reset_embeddings.py --dim 768");
    }
}
