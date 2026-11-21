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
        var builder = new NpgsqlDataSourceBuilder(PostgresConnectionString.WithPoolBounds(
            connectionString, settings.DbPoolMinSize, settings.DbPoolMaxSize));
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

        // Azure Database for PostgreSQL requires TLS and the URL carries it as ?sslmode=require.
        // Without a sslmode the Npgsql default stands, exactly as before.
        var sslMode = QueryValue(uri.Query, "sslmode");
        if (sslMode is not null)
        {
            ApplySslMode(builder, sslMode);
        }

        return builder.ConnectionString;
    }

    /// <summary>
    /// Maps libpq <c>sslmode</c> onto the Npgsql enum one to one (the Npgsql 10 enum follows libpq; the
    /// old TrustServerCertificate flag is obsolete). An unknown value fails at startup rather than
    /// silently dropping TLS.
    /// </summary>
    public static void ApplySslMode(NpgsqlConnectionStringBuilder builder, string sslMode)
    {
        switch (sslMode.Trim().ToLowerInvariant())
        {
            case "disable":
                builder.SslMode = SslMode.Disable;
                break;
            case "allow":
                builder.SslMode = SslMode.Allow;
                break;
            case "prefer":
                builder.SslMode = SslMode.Prefer;
                break;
            case "require":
                builder.SslMode = SslMode.Require;
                break;
            case "verify-ca":
                builder.SslMode = SslMode.VerifyCA;
                break;
            case "verify-full":
                builder.SslMode = SslMode.VerifyFull;
                break;
            default:
                throw new ArgumentException(
                    $"DATABASE_URL sslmode='{sslMode}' is not supported. Use disable, allow, prefer, require, verify-ca or verify-full.");
        }
    }

    /// <summary>
    /// Applies <c>DB_POOL_MIN_SIZE</c> / <c>DB_POOL_MAX_SIZE</c> when set; a null bound leaves whatever the
    /// connection string (or Npgsql's default) says.
    /// </summary>
    public static string WithPoolBounds(string connectionString, int? minSize, int? maxSize)
    {
        if (minSize is null && maxSize is null)
        {
            return connectionString;
        }

        var builder = new NpgsqlConnectionStringBuilder(connectionString);
        var min = minSize ?? builder.MinPoolSize;
        var max = maxSize ?? builder.MaxPoolSize;
        if (min < 0 || max < 1 || min > max)
        {
            throw new InvalidOperationException(
                $"DB_POOL_MIN_SIZE={min} / DB_POOL_MAX_SIZE={max} is invalid (need 0 <= min <= max, max >= 1).");
        }

        builder.MinPoolSize = min;
        builder.MaxPoolSize = max;
        return builder.ConnectionString;
    }

    private static string? QueryValue(string query, string key)
    {
        foreach (var pair in query.TrimStart('?').Split('&', StringSplitOptions.RemoveEmptyEntries))
        {
            var parts = pair.Split('=', 2);
            if (parts[0].Equals(key, StringComparison.OrdinalIgnoreCase))
            {
                return parts.Length > 1 ? Uri.UnescapeDataString(parts[1]) : string.Empty;
            }
        }

        return null;
    }
}
