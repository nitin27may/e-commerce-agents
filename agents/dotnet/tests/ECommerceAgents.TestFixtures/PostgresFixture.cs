using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using Testcontainers.PostgreSql;
using Xunit;

namespace ECommerceAgents.TestFixtures;

/// <summary>
/// One Postgres testcontainer per test session. Schema loaded from <c>docker/postgres/init.sql</c> plus <c>docker/postgres/migrations/*.sql</c>
/// so the test DB matches production. Never mock asyncpg/Npgsql — tests run against a real DB.
/// </summary>
public sealed class PostgresFixture : IAsyncLifetime
{
    // Match the production image — init.sql declares the `vector` extension
    // and the plain `postgres:16` image doesn't ship pgvector. Image is passed
    // to the constructor directly (Testcontainers.PostgreSql>=4.x obsoletes the
    // parameterless PostgreSqlBuilder() + .WithImage() chain).
    public PostgreSqlContainer Container { get; } = new PostgreSqlBuilder("pgvector/pgvector:pg16")
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

    // Same rule as scripts/migrate.py::checksum_of: CRLF folded to LF before hashing, so a Windows
    // checkout and a Linux one record the same checksum.
    private static byte[] LfNormalised(byte[] raw)
    {
        var result = new List<byte>(raw.Length);
        for (var i = 0; i < raw.Length; i++)
        {
            if (raw[i] == (byte)'\r' && i + 1 < raw.Length && raw[i + 1] == (byte)'\n')
            {
                continue;
            }

            result.Add(raw[i]);
        }

        return result.ToArray();
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

        // Same recipe as scripts/migrate.py: the frozen baseline, then docker/postgres/migrations/*.sql
        // in filename order, each recorded in schema_migrations (which /ready checks for).
        var postgresDir = Path.Combine(dir.FullName, "docker", "postgres");
        var files = new List<(string Version, string Path)> { ("0001_baseline", Path.Combine(postgresDir, "init.sql")) };
        var migrationsDir = Path.Combine(postgresDir, "migrations");
        if (Directory.Exists(migrationsDir))
        {
            files.AddRange(Directory.GetFiles(migrationsDir, "*.sql")
                .OrderBy(f => Path.GetFileName(f), StringComparer.Ordinal)
                .Select(f => (Path.GetFileNameWithoutExtension(f), f)));
        }

        await using var conn = new Npgsql.NpgsqlConnection(ConnectionString);
        await conn.OpenAsync();
        await using (var create = new Npgsql.NpgsqlCommand(
            @"CREATE TABLE IF NOT EXISTS schema_migrations (
                  version TEXT PRIMARY KEY,
                  checksum TEXT NOT NULL,
                  applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())", conn))
        {
            await create.ExecuteNonQueryAsync();
        }

        foreach (var (version, path) in files)
        {
            var bytes = await File.ReadAllBytesAsync(path);
            await using var cmd = new Npgsql.NpgsqlCommand(System.Text.Encoding.UTF8.GetString(bytes), conn);
            await cmd.ExecuteNonQueryAsync();
            await using var record = new Npgsql.NpgsqlCommand(
                "INSERT INTO schema_migrations (version, checksum) VALUES (@v, @c)", conn);
            record.Parameters.AddWithValue("v", version);
            record.Parameters.AddWithValue("c", Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(LfNormalised(bytes))).ToLowerInvariant());
            await record.ExecuteNonQueryAsync();
        }
    }
}

[CollectionDefinition(nameof(PostgresCollection))]
public sealed class PostgresCollection : ICollectionFixture<PostgresFixture>
{
    // Marker type — tests that use the DB reference [Collection(nameof(PostgresCollection))].
}
