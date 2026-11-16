using ECommerceAgents.Orchestrator.Routes;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using StackExchange.Redis;
using System.Net;
using System.Net.Http.Json;
using Xunit;

namespace ECommerceAgents.Orchestrator.Tests;

/// <summary>
/// The auth bucket (10/60 s per client in production, 3 here) on signup, login and refresh,
/// against a real Redis. Like <c>RateLimiterRedisTests</c> these return early when Redis is not
/// on localhost:6379 - CI and the local stack run one.
/// </summary>
[Collection(nameof(LocalPostgresCollection))]
public sealed class AuthRateLimitTests(PostgresFixture pg)
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

    private HttpClient Client(string redisUrl, out DatabasePool pool)
    {
        var settings = new AgentSettings
        {
            DatabaseUrl = pg.ConnectionString,
            RedisUrl = redisUrl,
            RateLimitEnabled = true,
            RateLimitAuthMaxRequests = 3,
            RateLimitAuthWindowSeconds = 60,
            RateLimitTrustedProxyHops = 1,
        };
        pool = new DatabasePool(settings);
        return OrchestratorTestHost.Create(pool, r => r.MapAuthRoutes(), settings).CreateClient();
    }

    private static string RandomIp() => $"10.{Random.Shared.Next(1, 255)}.{Random.Shared.Next(1, 255)}.{Random.Shared.Next(1, 255)}";

    private static async Task<HttpStatusCode> LoginAsync(HttpClient c, string forwardedFor)
    {
        var req = new HttpRequestMessage(HttpMethod.Post, "/api/auth/login")
        {
            Content = JsonContent.Create(new { email = "nobody@example.com", password = "wrong-password" }),
        };
        req.Headers.Add("X-Forwarded-For", forwardedFor);
        return (await c.SendAsync(req)).StatusCode;
    }

    [Fact]
    public async Task FourthRequestFromOneClientIsRejected_AndSpoofedLeftEntriesDoNotHelp()
    {
        if (!await RedisAvailableAsync())
        {
            return;
        }

        var c = Client(RedisUrl, out var pool);
        await using var _ = pool;
        var ip = RandomIp();

        for (var i = 0; i < 3; i++)
        {
            // Left-hand entries change every call; the proxy-appended right-most one does not.
            (await LoginAsync(c, $"9.9.9.{i}, {ip}")).Should().Be(HttpStatusCode.Unauthorized);
        }

        (await LoginAsync(c, $"9.9.9.99, {ip}")).Should().Be(HttpStatusCode.TooManyRequests);
        // A different client is unaffected.
        (await LoginAsync(c, RandomIp())).Should().Be(HttpStatusCode.Unauthorized);
    }

    [Fact]
    public async Task SignupAndRefreshShareTheBucket()
    {
        if (!await RedisAvailableAsync())
        {
            return;
        }

        var c = Client(RedisUrl, out var pool);
        await using var _ = pool;
        var ip = RandomIp();

        async Task<HttpStatusCode> Post(string path, object body)
        {
            var req = new HttpRequestMessage(HttpMethod.Post, path) { Content = JsonContent.Create(body) };
            req.Headers.Add("X-Forwarded-For", ip);
            return (await c.SendAsync(req)).StatusCode;
        }

        await Post("/api/auth/refresh", new { refresh_token = "junk" });
        await Post("/api/auth/refresh", new { refresh_token = "junk" });
        await Post("/api/auth/signup", new { email = "not-an-email", password = "x", name = "x" });
        (await Post("/api/auth/refresh", new { refresh_token = "junk" })).Should().Be(HttpStatusCode.TooManyRequests);
    }

    [Fact]
    public async Task FailsOpenWhenRedisIsDown()
    {
        var c = Client("localhost:1,connectTimeout=200,abortConnect=false", out var pool);
        await using var _ = pool;
        var ip = RandomIp();

        for (var i = 0; i < 6; i++)
        {
            (await LoginAsync(c, ip)).Should().Be(HttpStatusCode.Unauthorized);
        }
    }
}
