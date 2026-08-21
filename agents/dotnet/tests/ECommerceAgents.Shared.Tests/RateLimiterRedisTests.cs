using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.RateLimiting;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using StackExchange.Redis;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// Exercises the sliding window against a real Redis, because the window logic
/// lives in a Lua script and is therefore only meaningfully testable there —
/// mocking <c>ScriptEvaluateAsync</c> would assert that we wrote the call, not
/// that the algorithm bounds anything.
///
/// Skips when Redis is unreachable rather than failing, matching how the
/// Postgres-backed tests treat their dependency: the local stack runs Redis
/// (<c>docker-compose.yml</c>), CI does too, and a developer without it should
/// not see a red build for infrastructure they were never asked to run.
/// </summary>
public sealed class RateLimiterRedisTests
{
    private const string RedisUrl = "localhost:6379,connectTimeout=500,abortConnect=false";

    private static async Task<bool> RedisAvailableAsync()
    {
        try
        {
            await using var mux = await ConnectionMultiplexer.ConnectAsync(RedisUrl);
            return mux.IsConnected;
        }
        catch
        {
            return false;
        }
    }

    private static SlidingWindowRateLimiter Build(int max) =>
        new(
            new AgentSettings
            {
                RateLimitEnabled = true,
                RedisUrl = RedisUrl,
                RateLimitMaxRequests = max,
                RateLimitWindowSeconds = 60.0,
            },
            NullLogger<SlidingWindowRateLimiter>.Instance
        );

    [Fact]
    public async Task AllowsUpToTheLimitThenDenies()
    {
        if (!await RedisAvailableAsync())
        {
            return;
        }

        // Unique key per run so repeated local runs don't inherit each other's window.
        var key = $"ratelimit:test:{Guid.NewGuid():N}";
        var limiter = Build(max: 3);

        for (var i = 1; i <= 3; i++)
        {
            (await limiter.TryAcquireAsync(key)).Should().BeTrue($"request {i} is within the limit of 3");
        }

        (await limiter.TryAcquireAsync(key)).Should().BeFalse("the 4th request exceeds the window's limit");
    }

    [Fact]
    public async Task LimitsAreIndependentPerKey()
    {
        if (!await RedisAvailableAsync())
        {
            return;
        }

        // One user exhausting their budget must not lock out everyone else —
        // the whole reason the key is per-user rather than global.
        var limiter = Build(max: 1);
        var alice = $"ratelimit:test:alice:{Guid.NewGuid():N}";
        var bob = $"ratelimit:test:bob:{Guid.NewGuid():N}";

        (await limiter.TryAcquireAsync(alice)).Should().BeTrue();
        (await limiter.TryAcquireAsync(alice)).Should().BeFalse();

        (await limiter.TryAcquireAsync(bob)).Should().BeTrue("bob has his own window");
    }

    /// <summary>
    /// The reason the check-and-record is one Lua script rather than a
    /// count-then-write pair: concurrent requests must not slip past the limit
    /// between the two round trips.
    /// </summary>
    [Fact]
    public async Task ConcurrentRequests_DoNotExceedTheLimit()
    {
        if (!await RedisAvailableAsync())
        {
            return;
        }

        var key = $"ratelimit:test:{Guid.NewGuid():N}";
        var limiter = Build(max: 5);

        var results = await Task.WhenAll(
            Enumerable.Range(0, 20).Select(_ => limiter.TryAcquireAsync(key))
        );

        results.Count(allowed => allowed).Should().Be(5, "exactly the limit, no matter the concurrency");
    }
}
