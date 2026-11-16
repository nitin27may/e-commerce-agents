using Dapper;
using ECommerceAgents.Shared.Data;
using Npgsql;

namespace ECommerceAgents.Shared.Auth;

/// <summary>
/// Refresh-token rotation with reuse detection (<c>AuthMode=local</c>) - the .NET twin of Python's
/// <c>shared/refresh_tokens.py</c>, over the same <c>refresh_tokens</c> table (migration 0003).
/// </summary>
/// <remarks>
/// A refresh consumes the presented token and issues a successor in the same family. A token
/// presented after it was already consumed can only be a copy, so the whole family is revoked.
/// The rotation runs under <c>SELECT ... FOR UPDATE</c>, so two simultaneous refreshes with one
/// token serialise and exactly one wins - clients must single-flight their refresh.
/// Tokens without a <c>jti</c> (issued before this feature) are rejected: one forced re-login.
///
/// A family also has an absolute expiry (<c>family_expires_at</c>, migration 0005, the configured
/// max days from the login that started it) that every successor inherits; a token's own expiry
/// never outlives it and rotation refuses once it has passed. Rotation prunes a bounded batch of
/// dead rows so the table does not grow forever.
/// </remarks>
public static class RefreshTokenStore
{
    public enum RejectReason { None, Unknown, Expired, Reuse }

    public sealed record Issued(string Token, string Jti);

    /// <summary>Mint and record a refresh token. A new login/signup passes no family and starts one.</summary>
    public static async Task<Issued> IssueAsync(
        NpgsqlConnection conn,
        JwtTokenService jwt,
        string email,
        string role,
        int familyMaxDays,
        Guid? familyId = null,
        DateTime? familyExpiresAt = null,
        NpgsqlTransaction? tx = null
    )
    {
        var jti = Guid.NewGuid().ToString();
        var now = DateTime.UtcNow;
        // A new family gets its absolute expiry now; a successor passes the one it inherited.
        var familyExpires = familyExpiresAt ?? now.AddDays(familyMaxDays);
        var expires = now.Add(jwt.RefreshTokenLifetime);
        if (expires > familyExpires)
        {
            expires = familyExpires;
        }
        await conn.ExecuteAsync(
            @"INSERT INTO refresh_tokens (jti, user_email, family_id, expires_at, family_expires_at)
              VALUES (@jti, @email, @family, @expires, @familyExpires)",
            new { jti, email, family = familyId ?? Guid.NewGuid(), expires, familyExpires },
            tx
        );
        return new Issued(jwt.IssueRefreshToken(email, role, jti), jti);
    }

    /// <summary>
    /// Consume the token identified by <paramref name="jti"/> (claimed for <paramref name="email"/>) and
    /// return its successor, or the reason it was refused.
    /// </summary>
    public static async Task<(Issued? Token, RejectReason Reason)> RotateAsync(
        DatabasePool pool,
        JwtTokenService jwt,
        string? jti,
        string email,
        string role,
        int familyMaxDays
    )
    {
        if (string.IsNullOrEmpty(jti))
        {
            return (null, RejectReason.Unknown);
        }

        await using var conn = await pool.OpenAsync();
        await using var tx = await conn.BeginTransactionAsync();

        var row = await conn.QueryFirstOrDefaultAsync(
            "SELECT user_email, family_id, expires_at, family_expires_at, revoked_at FROM refresh_tokens WHERE jti = @jti FOR UPDATE",
            new { jti },
            tx
        );

        if (row is null || !string.Equals((string)row.user_email, email, StringComparison.Ordinal))
        {
            await tx.RollbackAsync();
            return (null, RejectReason.Unknown);
        }

        if (row.revoked_at is not null)
        {
            // Committed (not rolled back): the family revocation is the point.
            await conn.ExecuteAsync(
                "UPDATE refresh_tokens SET revoked_at = NOW() WHERE family_id = @family AND revoked_at IS NULL",
                new { family = (Guid)row.family_id },
                tx
            );
            await tx.CommitAsync();
            return (null, RejectReason.Reuse);
        }

        // Either this token ran out, or the session as a whole hit its absolute cap.
        if ((DateTime)row.expires_at <= DateTime.UtcNow || (DateTime)row.family_expires_at <= DateTime.UtcNow)
        {
            await tx.RollbackAsync();
            return (null, RejectReason.Expired);
        }

        var next = await IssueAsync(
            conn, jwt, email, role, familyMaxDays, (Guid)row.family_id, (DateTime)row.family_expires_at, tx);
        await conn.ExecuteAsync(
            "UPDATE refresh_tokens SET revoked_at = NOW(), replaced_by = @next WHERE jti = @jti",
            new { next = next.Jti, jti },
            tx
        );
        await tx.CommitAsync();
        await PruneAsync(conn);
        return (next, RejectReason.None);
    }

    private const int PruneBatch = 100;

    /// <summary>
    /// Deletes a bounded batch of dead rows: expired for over a day, or in a family with no live
    /// token. A rotated token stays until it expires (replaying it is how reuse is detected). Best
    /// effort: a failure never surfaces to a caller whose rotation already succeeded.
    /// </summary>
    internal static async Task PruneAsync(NpgsqlConnection conn)
    {
        try
        {
            await conn.ExecuteAsync(
                @"DELETE FROM refresh_tokens
                   WHERE jti IN (
                         SELECT r.jti FROM refresh_tokens r
                          WHERE r.expires_at < NOW() - interval '1 day'
                             OR (r.revoked_at IS NOT NULL
                                 AND r.revoked_at < NOW() - interval '1 day'
                                 AND NOT EXISTS (SELECT 1 FROM refresh_tokens x
                                                  WHERE x.family_id = r.family_id AND x.revoked_at IS NULL))
                          LIMIT @batch)",
                new { batch = PruneBatch });
        }
        catch (Exception)
        {
            // Pruning is housekeeping; the next rotation tries again.
        }
    }
}
