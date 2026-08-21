using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.RateLimiting;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// Issue #30. The sliding-window arithmetic lives in a Lua script executed by
/// Redis, so these cover the parts that are decidable without one: key
/// derivation, the disabled switch, and the deliberate fail-open posture.
/// The window behaviour itself is exercised against a real Redis in
/// <see cref="RateLimiterRedisTests"/>.
/// </summary>
public sealed class RateLimiterTests
{
    private static SlidingWindowRateLimiter Build(AgentSettings settings) =>
        new(settings, NullLogger<SlidingWindowRateLimiter>.Instance);

    [Fact]
    public void KeyFor_AnonymousCallers_AreLimitedByIp()
    {
        // The case that matters most: /api/chat serves anonymous storefront
        // traffic, so an unauthenticated caller must still be bounded.
        SlidingWindowRateLimiter.KeyFor(null, "203.0.113.7").Should().Be("ratelimit:chat:ip:203.0.113.7");
        SlidingWindowRateLimiter.KeyFor("", "203.0.113.7").Should().Be("ratelimit:chat:ip:203.0.113.7");
        SlidingWindowRateLimiter.KeyFor("anonymous", "203.0.113.7").Should().Be("ratelimit:chat:ip:203.0.113.7");
    }

    [Fact]
    public void KeyFor_SignedInCallers_AreLimitedPerUser_NotPerIp()
    {
        // Otherwise everyone behind one office NAT would share a bucket.
        SlidingWindowRateLimiter.KeyFor("alice@example.com", "203.0.113.7")
            .Should().Be("ratelimit:chat:user:alice@example.com");
    }

    [Fact]
    public void KeyFor_UnknownIp_StillProducesAStableKey()
    {
        SlidingWindowRateLimiter.KeyFor(null, null).Should().Be("ratelimit:chat:ip:unknown");
    }

    [Fact]
    public async Task TryAcquire_WhenDisabled_AllowsWithoutTouchingRedis()
    {
        // Points at a port nothing is listening on: if the limiter tried to
        // connect, this would be slow or throw rather than returning promptly.
        var settings = new AgentSettings { RateLimitEnabled = false, RedisUrl = "localhost:6399" };

        (await Build(settings).TryAcquireAsync("ratelimit:chat:ip:test")).Should().BeTrue();
    }

    /// <summary>
    /// Fails open by design: an outage of the rate limiter must not take chat
    /// down with it. Deliberately the opposite of <c>HitlGate</c>'s
    /// fail-closed posture — one protects spend, the other protects money
    /// leaving the business.
    /// </summary>
    [Fact]
    public async Task TryAcquire_WhenRedisIsUnreachable_FailsOpen()
    {
        var settings = new AgentSettings { RateLimitEnabled = true, RedisUrl = "localhost:6399,connectTimeout=200,abortConnect=false" };

        (await Build(settings).TryAcquireAsync("ratelimit:chat:ip:test")).Should().BeTrue();
    }
}
