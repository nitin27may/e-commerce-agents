using Dapper;
using ECommerceAgents.Shared.Data;
using Microsoft.Extensions.Logging;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace ECommerceAgents.Shared.Idempotency;

/// <summary>
/// Makes a money-moving operation safe to retry — the .NET twin of Python's
/// <c>shared/idempotency.py</c>.
/// </summary>
/// <remarks>
/// The problem this closes is not concurrency. <c>ReturnTools.ProcessRefund</c> already
/// guards two *simultaneous* calls with a status-checked UPDATE. What nothing detected is
/// "this exact request already succeeded": a client that times out and retries, or a user
/// double-clicking Approve, re-runs the mutation. Before this, a database constraint was
/// the only thing standing between that and two refunds on .NET, and constraints produce
/// an error where the caller wanted the original success replayed.
///
/// The protocol, backed by <c>idempotency_keys</c>:
///
/// 1. <b>Reserve</b> with <c>INSERT ... ON CONFLICT DO NOTHING RETURNING key</c>. A row
///    back means this caller won.
/// 2. <b>Conflict.</b> A <c>completed</c> row replays its cached result rather than
///    re-executing. A young <c>in_progress</c> row is a live duplicate and is refused. One
///    older than <see cref="StaleAfter"/> is treated as abandoned — the process that
///    reserved it crashed before completing or releasing — and taken over, because the
///    alternative is a key that blocks its own operation forever.
/// 3. <b>Complete or release.</b> Success caches the result; failure deletes the
///    reservation so a genuine retry after a real error is not permanently blocked.
///
/// Returns the same shape the call site already returns on every path rather than
/// throwing, matching the <c>{ error = ... }</c> convention used throughout
/// <c>Shared/Tools/</c>, so a caller needs no special-case handling to adopt it.
/// </remarks>
public sealed class IdempotencyGuard(DatabasePool pool, ILogger<IdempotencyGuard>? logger = null)
{
    private readonly DatabasePool _pool = pool;
    private readonly ILogger<IdempotencyGuard>? _logger = logger;

    /// <summary>
    /// How old an <c>in_progress</c> reservation must be before it is assumed abandoned
    /// rather than live. Matches Python's <c>_STALE_AFTER</c>.
    /// </summary>
    public static readonly TimeSpan StaleAfter = TimeSpan.FromSeconds(60);

    private const string ConflictMessage =
        "A request for this action is already being processed. Please wait a moment and try again.";

    /// <summary>
    /// Runs <paramref name="operation"/> at most once for a given
    /// <paramref name="scope"/>, <paramref name="identity"/> and argument set.
    /// </summary>
    /// <param name="scope">Names the operation, e.g. <c>process_refund</c>.</param>
    /// <param name="identity">
    /// Whose action this is. Must be the *target customer*, not an acting admin — keying
    /// an approval on the approver lets two admins each release the same refund.
    /// </param>
    /// <param name="arguments">Anything that makes this call distinct from another.</param>
    public async Task<object> ExecuteAsync(
        string scope,
        string? identity,
        object arguments,
        Func<Task<object>> operation,
        CancellationToken ct = default
    )
    {
        ArgumentNullException.ThrowIfNull(operation);

        if (string.IsNullOrWhiteSpace(identity))
        {
            // No stable identity to scope a key to, so every call would collide with
            // every other caller's. Running unguarded is strictly better than that;
            // Python makes the same call for the same reason.
            _logger?.LogWarning("idempotency.skipped_no_identity scope={Scope}", scope);
            return await operation();
        }

        var key = CanonicalKey(scope, identity, arguments);
        var (reserved, conflictOrCached) = await ReserveAsync(key, scope, ct);

        if (!reserved)
        {
            return conflictOrCached ?? new { error = ConflictMessage };
        }

        try
        {
            var result = await operation();
            await CompleteAsync(key, result, ct);
            return result;
        }
        catch
        {
            await ReleaseAsync(key, ct);
            throw;
        }
    }

    /// <summary>sha256 over (identity, scope, canonical args), same inputs as Python's.</summary>
    private static string CanonicalKey(string scope, string identity, object arguments)
    {
        var canonical = JsonSerializer.Serialize(arguments);
        var digest = Convert.ToHexStringLower(
            SHA256.HashData(Encoding.UTF8.GetBytes($"{identity}:{scope}:{canonical}")));
        return $"{scope}:{digest}";
    }

    private async Task<(bool Reserved, object? ConflictOrCached)> ReserveAsync(
        string key,
        string scope,
        CancellationToken ct,
        int depth = 0
    )
    {
        await using var conn = await _pool.OpenAsync();

        var won = await conn.ExecuteScalarAsync<string?>(
            @"INSERT INTO idempotency_keys (key, scope, status)
              VALUES (@key, @scope, 'in_progress')
              ON CONFLICT (key) DO NOTHING
              RETURNING key",
            new { key, scope });

        if (won is not null)
        {
            return (true, null);
        }

        var existing = await conn.QueryFirstOrDefaultAsync(
            "SELECT status, result, created_at FROM idempotency_keys WHERE key = @key",
            new { key });

        if (existing is null)
        {
            // Raced with a concurrent release cleaning up a failed attempt, so the key is
            // free again. Retry once — bounded, because an unbounded recursion here would
            // turn a pathological retry loop into a stack overflow.
            return depth >= 1 ? (false, new { error = ConflictMessage }) : await ReserveAsync(key, scope, ct, depth + 1);
        }

        if ((string)existing.status == "completed")
        {
            _logger?.LogInformation("idempotency.replay scope={Scope} key={Key}", scope, key);
            return (false, DecodeResult(existing.result));
        }

        var createdAt = (DateTime)existing.created_at;
        var age = DateTime.UtcNow - DateTime.SpecifyKind(createdAt, DateTimeKind.Utc);
        if (age > StaleAfter)
        {
            // Guarded on created_at so only one caller can reclaim: whoever's UPDATE
            // matches the timestamp they read wins, everyone else falls through.
            var taken = await conn.ExecuteScalarAsync<string?>(
                @"UPDATE idempotency_keys SET created_at = NOW()
                   WHERE key = @key AND status = 'in_progress' AND created_at = @createdAt
               RETURNING key",
                new { key, createdAt });

            if (taken is not null)
            {
                _logger?.LogWarning(
                    "idempotency.reclaimed_stale scope={Scope} key={Key} age_s={Age:F1}",
                    scope, key, age.TotalSeconds);
                return (true, null);
            }
        }

        _logger?.LogInformation(
            "idempotency.conflict scope={Scope} key={Key} status={Status}", scope, key, (string)existing.status);
        return (false, new { error = ConflictMessage });
    }

    /// <summary>
    /// Npgsql hands JSONB back as raw text unless a codec is registered, so this decodes
    /// explicitly rather than casting — the same trap <c>hitl.py</c>'s <c>_decode_jsonb</c>
    /// exists for on the Python side.
    /// </summary>
    private static object DecodeResult(object? raw)
    {
        if (raw is not string json || string.IsNullOrWhiteSpace(json))
        {
            return new { };
        }
        try
        {
            return JsonDocument.Parse(json).RootElement.Clone();
        }
        catch (JsonException)
        {
            return new { };
        }
    }

    private async Task CompleteAsync(string key, object result, CancellationToken ct)
    {
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            @"UPDATE idempotency_keys
                 SET status = 'completed', result = @result::jsonb, completed_at = NOW()
               WHERE key = @key",
            new { key, result = JsonSerializer.Serialize(result) });
    }

    private async Task ReleaseAsync(string key, CancellationToken ct)
    {
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            "DELETE FROM idempotency_keys WHERE key = @key AND status = 'in_progress'",
            new { key });
    }
}
