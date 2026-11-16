using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Data;
using Npgsql;

namespace ECommerceAgents.Shared.Agents;

/// <summary>
/// Startup check: <c>EMBEDDING_DIM</c> must equal the width of the stored vector columns. The .NET twin of
/// Python's <c>shared/embedding_check.py</c>.
/// </summary>
/// <remarks>
/// <c>product_embeddings.embedding</c> and <c>agent_memories.embedding</c> are <c>vector(1536)</c> in the frozen
/// baseline. A different <c>EMBEDDING_DIM</c> (or embedding model) without changing the columns used to
/// surface only on the first insert or query, as an opaque pgvector error. This fails at startup with a message
/// that names the fix. Changing a column destroys its vectors, so the fix is the explicit operator action
/// <c>scripts/reset_embeddings.py --dim N</c>, deliberately not a migration.
/// </remarks>
public static class EmbeddingDimensionCheck
{
    public static readonly string[] Tables = ["product_embeddings", "agent_memories"];

    private const string ColumnDimsSql = """
        SELECT c.relname, a.atttypmod
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname = ANY(@tables) AND a.attname = 'embedding' AND NOT a.attisdropped
        """;

    /// <summary>Declared <c>vector(N)</c> width per table; null for an unconstrained <c>vector</c>. A missing table is absent.</summary>
    public static async Task<Dictionary<string, int?>> ColumnDimensionsAsync(NpgsqlConnection conn, CancellationToken ct = default)
    {
        var result = new Dictionary<string, int?>();
        await using var cmd = new NpgsqlCommand(ColumnDimsSql, conn);
        cmd.Parameters.AddWithValue("tables", Tables);
        await using var reader = await cmd.ExecuteReaderAsync(ct);
        while (await reader.ReadAsync(ct))
        {
            // pgvector stores the declared dimension directly in atttypmod; -1 means "no dimension".
            var dim = reader.GetInt32(1);
            result[reader.GetString(0)] = dim > 0 ? dim : null;
        }

        return result;
    }

    /// <summary>One line per table whose declared width differs from <paramref name="configured"/>.</summary>
    public static List<string> Problems(int configured, IReadOnlyDictionary<string, int?> actual) =>
        actual.OrderBy(kv => kv.Key, StringComparer.Ordinal)
            .Where(kv => kv.Value is { } d && d != configured)
            .Select(kv => $"{kv.Key}.embedding is vector({kv.Value})")
            .ToList();

    /// <summary>The failure message, naming the fix.</summary>
    public static string Message(int configured, IEnumerable<string> problems) =>
        $"EMBEDDING_DIM={configured} but {string.Join("; ", problems)}. Either set EMBEDDING_DIM to the column's size, "
        + $"or run `uv run --project agents/python python scripts/reset_embeddings.py --dim {configured}` to change the columns "
        + "(this deletes every stored embedding; re-run the seeder or scripts/generate_embeddings.py afterwards).";

    /// <summary>Throws <see cref="InvalidOperationException"/> naming the fix if a column's width differs.</summary>
    public static async Task VerifyAsync(NpgsqlConnection conn, AgentSettings settings, CancellationToken ct = default)
    {
        var problems = Problems(settings.EmbeddingDim, await ColumnDimensionsAsync(conn, ct));
        if (problems.Count > 0)
        {
            throw new InvalidOperationException(Message(settings.EmbeddingDim, problems));
        }
    }

    /// <summary>Startup hook: checks on a pooled connection.</summary>
    public static async Task VerifyAsync(DatabasePool pool, AgentSettings settings, CancellationToken ct = default)
    {
        await using var conn = await pool.OpenAsync(ct);
        await VerifyAsync(conn, settings, ct);
    }
}
