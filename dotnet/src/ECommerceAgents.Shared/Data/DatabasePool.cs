using ECommerceAgents.Shared.Configuration;
using Npgsql;

namespace ECommerceAgents.Shared.Data;

/// <summary>
/// Thin wrapper around <see cref="NpgsqlDataSource"/> — equivalent to
/// Python's <c>shared/db.py</c> asyncpg pool. Agents register a singleton
/// <see cref="DatabasePool"/> in DI and pull connections from it rather
/// than building a <see cref="NpgsqlDataSourceBuilder"/> ad-hoc.
/// </summary>
public sealed class DatabasePool : IAsyncDisposable
{
    private readonly NpgsqlDataSource _dataSource;

    public DatabasePool(AgentSettings settings)
    {
        var connectionString = PostgresConnectionString.FromUrl(settings.DatabaseUrl);
        var builder = new NpgsqlDataSourceBuilder(connectionString);
        _dataSource = builder.Build();
    }

    public NpgsqlDataSource DataSource => _dataSource;

    public ValueTask<NpgsqlConnection> OpenAsync(CancellationToken ct = default) =>
        _dataSource.OpenConnectionAsync(ct);

    public ValueTask DisposeAsync() => _dataSource.DisposeAsync();
}

/// <summary>
/// Converts a Python-style DSN (<c>postgresql://user:pass@host:port/db</c>)
/// into an Npgsql keyword connection string.
/// </summary>
public static class PostgresConnectionString
{
    public static string FromUrl(string url)
    {
        if (string.IsNullOrWhiteSpace(url))
        {
            throw new ArgumentException("DATABASE_URL is empty", nameof(url));
        }

        // Let Npgsql handle keyword strings directly.
        if (!url.Contains("://", StringComparison.Ordinal))
        {
            return url;
        }

        var uri = new Uri(url);
        var userInfo = uri.UserInfo.Split(':', 2);
        var username = Uri.UnescapeDataString(userInfo[0]);
        var password = userInfo.Length > 1 ? Uri.UnescapeDataString(userInfo[1]) : string.Empty;
        var database = uri.AbsolutePath.TrimStart('/');

        var builder = new NpgsqlConnectionStringBuilder
        {
            Host = uri.Host,
            Port = uri.Port > 0 ? uri.Port : 5432,
            Username = username,
            Password = password,
            Database = database,
            IncludeErrorDetail = true,
        };
        return builder.ConnectionString;
    }
}
