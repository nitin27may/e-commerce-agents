using Dapper;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Idempotency;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// The .NET stack had no idempotency at all: a database constraint was the only thing
/// between a double-click and two refunds.
/// </summary>
/// <remarks>
/// Concurrency was already covered — status-guarded UPDATEs stop two simultaneous calls
/// from both succeeding. What nothing covered is a *sequential* retry after the first
/// call committed: the client that times out and tries again, which is the ordinary case
/// rather than the exotic one.
/// </remarks>
[Collection(nameof(LocalPostgresCollection))]
public sealed class IdempotencyGuardTests : IAsyncLifetime
{
    private readonly PostgresFixture _pg;
    private DatabasePool _pool = null!;
    private IdempotencyGuard _guard = null!;

    public IdempotencyGuardTests(PostgresFixture pg) => _pg = pg;

    public async Task InitializeAsync()
    {
        _pool = new DatabasePool(new AgentSettings { DatabaseUrl = _pg.ConnectionString });
        _guard = new IdempotencyGuard(_pool);
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync("TRUNCATE idempotency_keys");
    }

    public async Task DisposeAsync() => await _pool.DisposeAsync();

    [Fact]
    public async Task ARetryReplaysTheFirstResult_WithoutRunningTheOperationAgain()
    {
        var runs = 0;
        Task<object> Refund()
        {
            runs++;
            return Task.FromResult<object>(new { status = "refunded", amount = 803.46m });
        }

        var first = await _guard.ExecuteAsync("process_refund", "c@example.com", new { returnId = "r1" }, Refund);
        var second = await _guard.ExecuteAsync("process_refund", "c@example.com", new { returnId = "r1" }, Refund);

        runs.Should().Be(1, "the money must move once however many times the client asks");
        Json(first).GetProperty("status").GetString().Should().Be("refunded");
        Json(second).GetProperty("status").GetString().Should().Be("refunded",
            "a retry should be indistinguishable from the original success, not an error");
    }

    [Fact]
    public async Task DifferentArguments_AreDifferentOperations()
    {
        var runs = 0;
        Task<object> Refund() { runs++; return Task.FromResult<object>(new { ok = true }); }

        await _guard.ExecuteAsync("process_refund", "c@example.com", new { returnId = "r1" }, Refund);
        await _guard.ExecuteAsync("process_refund", "c@example.com", new { returnId = "r2" }, Refund);

        runs.Should().Be(2, "two genuinely different refunds are not a duplicate");
    }

    [Fact]
    public async Task DifferentIdentities_AreDifferentOperations()
    {
        var runs = 0;
        Task<object> Act() { runs++; return Task.FromResult<object>(new { ok = true }); }

        await _guard.ExecuteAsync("hitl_execute", "alice@example.com", new { id = "x" }, Act);
        await _guard.ExecuteAsync("hitl_execute", "bob@example.com", new { id = "x" }, Act);

        runs.Should().Be(2);
    }

    [Fact]
    public async Task AConcurrentDuplicate_IsRefusedRatherThanRunTwice()
    {
        var runs = 0;
        async Task<object> Slow()
        {
            Interlocked.Increment(ref runs);
            await Task.Delay(200);
            return new { status = "refunded" };
        }

        var results = await Task.WhenAll(
            _guard.ExecuteAsync("process_refund", "c@example.com", new { returnId = "r9" }, Slow),
            _guard.ExecuteAsync("process_refund", "c@example.com", new { returnId = "r9" }, Slow));

        runs.Should().Be(1);
        results.Count(r => Json(r).TryGetProperty("error", out _)).Should().Be(1,
            "the loser is told to wait, not silently handed a success it did not cause");
    }

    [Fact]
    public async Task AFailedOperationReleasesItsKey_SoAGenuineRetryIsNotBlockedForever()
    {
        var runs = 0;
        Task<object> Flaky()
        {
            runs++;
            return runs == 1
                ? throw new InvalidOperationException("transient")
                : Task.FromResult<object>(new { status = "refunded" });
        }

        var act = async () => await _guard.ExecuteAsync(
            "process_refund", "c@example.com", new { returnId = "r5" }, Flaky);
        await act.Should().ThrowAsync<InvalidOperationException>();

        // Without the release a transient database blip would poison the key permanently
        // and the customer could never be refunded at all.
        var retry = await _guard.ExecuteAsync("process_refund", "c@example.com", new { returnId = "r5" }, Flaky);

        runs.Should().Be(2);
        Json(retry).GetProperty("status").GetString().Should().Be("refunded");
    }

    [Fact]
    public async Task AnAbandonedReservationIsReclaimed_RatherThanBlockingForever()
    {
        await using var conn = await _pool.OpenAsync();
        var key = await PlantStaleReservationAsync(conn, "process_refund", "c@example.com", new { returnId = "r7" });

        var runs = 0;
        Task<object> Refund() { runs++; return Task.FromResult<object>(new { status = "refunded" }); }

        var result = await _guard.ExecuteAsync("process_refund", "c@example.com", new { returnId = "r7" }, Refund);

        runs.Should().Be(1, "a crashed reservation must not lock its own operation out permanently");
        Json(result).GetProperty("status").GetString().Should().Be("refunded");
        (await conn.ExecuteScalarAsync<string>(
            "SELECT status FROM idempotency_keys WHERE key = @key", new { key })).Should().Be("completed");
    }

    [Fact]
    public async Task WithNoIdentity_TheOperationStillRuns()
    {
        var runs = 0;
        Task<object> Act() { runs++; return Task.FromResult<object>(new { ok = true }); }

        // No stable identity means every caller's key would collide with every other's,
        // so running unguarded beats guarding wrongly. Same call Python makes.
        await _guard.ExecuteAsync("process_refund", null, new { returnId = "r1" }, Act);
        await _guard.ExecuteAsync("process_refund", "", new { returnId = "r1" }, Act);

        runs.Should().Be(2);
    }

    /// <summary>
    /// Plants the row a process would have left behind if it reserved a key and then
    /// died — in_progress, older than the stale window. Derives the key the same way
    /// <see cref="IdempotencyGuard"/> does so it is the row the guard will look for.
    /// </summary>
    private static async Task<string> PlantStaleReservationAsync(
        System.Data.Common.DbConnection conn,
        string scope,
        string identity,
        object args
    )
    {
        var canonical = JsonSerializer.Serialize(args);
        var digest = Convert.ToHexStringLower(
            SHA256.HashData(Encoding.UTF8.GetBytes($"{identity}:{scope}:{canonical}")));
        var key = $"{scope}:{digest}";

        await conn.ExecuteAsync(
            @"INSERT INTO idempotency_keys (key, scope, status, created_at)
              VALUES (@key, @scope, 'in_progress', NOW() - INTERVAL '5 minutes')",
            new { key, scope });
        return key;
    }

    private static JsonElement Json(object value) =>
        value is JsonElement e ? e : JsonSerializer.SerializeToElement(value);
}
