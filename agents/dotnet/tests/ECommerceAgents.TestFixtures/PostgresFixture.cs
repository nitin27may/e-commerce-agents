using System.IO;
using System.Threading.Tasks;
using Testcontainers.PostgreSql;
using Xunit;

namespace ECommerceAgents.TestFixtures;

/// <summary>
/// One Postgres testcontainer per test session. Schema loaded from <c>docker/postgres/init.sql</c>
/// so the test DB matches production. Never mock asyncpg/Npgsql — tests run against a real DB.
/// </summary>
public sealed class PostgresFixture : IAsyncLifetime
{
    public PostgreSqlContainer Container { get; } = new PostgreSqlBuilder()
        // Match the production image — init.sql declares the `vector` extension
        // and the plain `postgres:16` image doesn't ship pgvector.
        .WithImage("pgvector/pgvector:pg16")
        .WithDatabase("ecommerce_test")
        .WithUsername("test")
        .WithPassword("test")
        .Build();

    public string ConnectionString => Container.GetConnectionString();

    public async Task InitializeAsync()
    {
        await Container.StartAsync();
        await ApplySchemaAsync();
    }

    public async Task DisposeAsync()
    {
        await Container.DisposeAsync();
    }

    private async Task ApplySchemaAsync()
    {
        // Walk up to the repo root to find docker/postgres/init.sql. Keeps the schema single-sourced.
        var dir = new DirectoryInfo(Directory.GetCurrentDirectory());
        while (dir != null && !File.Exists(Path.Combine(dir.FullName, "docker", "postgres", "init.sql")))
        {
            dir = dir.Parent;
        }

        if (dir == null)
        {
            throw new FileNotFoundException(
                "Could not locate docker/postgres/init.sql walking up from the test working directory.");
        }

        var schema = await File.ReadAllTextAsync(Path.Combine(dir.FullName, "docker", "postgres", "init.sql"));
        await using var conn = new Npgsql.NpgsqlConnection(ConnectionString);
        await conn.OpenAsync();
        await using var cmd = new Npgsql.NpgsqlCommand(schema, conn);
        await cmd.ExecuteNonQueryAsync();
    }
}

[CollectionDefinition(nameof(PostgresCollection))]
public sealed class PostgresCollection : ICollectionFixture<PostgresFixture>
{
    // Marker type — tests that use the DB reference [Collection(nameof(PostgresCollection))].
}
