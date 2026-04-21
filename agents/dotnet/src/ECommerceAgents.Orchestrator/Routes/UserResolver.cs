using Dapper;
using ECommerceAgents.Shared.Data;
using Npgsql;

namespace ECommerceAgents.Orchestrator.Routes;

/// <summary>
/// Small shared helper: resolve the logged-in user's UUID from the email
/// the auth middleware already stamped on <c>RequestContext</c>. Python
/// routes pull <c>user_id</c> out of the JWT claim directly; .NET routes
/// look it up once per request via this helper to keep the auth middleware
/// lean.
/// </summary>
public static class UserResolver
{
    public static async Task<Guid?> ResolveUserIdAsync(DatabasePool pool, string email, CancellationToken ct = default)
    {
        if (string.IsNullOrEmpty(email))
        {
            return null;
        }

        await using var conn = await pool.OpenAsync(ct);
        return await ResolveUserIdAsync(conn, email);
    }

    public static async Task<Guid?> ResolveUserIdAsync(NpgsqlConnection conn, string email)
    {
        var row = await conn.QueryFirstOrDefaultAsync<Guid?>(
            "SELECT id FROM users WHERE email = @email",
            new { email }
        );
        return row;
    }
}
