using ECommerceAgents.Shared.Auth;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Health;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.TestHost;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Npgsql;
using System.Net;
using System.Text.Json;
using Xunit;

namespace ECommerceAgents.Orchestrator.Tests;

/// <summary>
/// <c>GET /ready</c> (#102). Runs behind the real <see cref="AgentAuthMiddleware"/> so the
/// "public like /health" claim is exercised, against the migrated Postgres testcontainer.
/// The JSON shape is asserted field by field because Python's <c>shared/readiness.py</c> returns
/// the same one and the compose healthchecks (and the keyless boot smoke) read it.
/// </summary>
[Collection(nameof(LocalPostgresCollection))]
public sealed class ReadinessTests(PostgresFixture pg)
{
    private const string DeadRedis = "127.0.0.1:1";

    private AgentSettings Settings(string? databaseUrl = null, string redis = DeadRedis) => new()
    {
        DatabaseUrl = databaseUrl ?? pg.ConnectionString,
        RedisUrl = redis,
        AgentSharedSecret = new string('s', 48),
        JwtSecret = new string('j', 48),
        Environment = "test",
        AuthMode = "local",
    };

    private static HttpClient ClientFor(AgentSettings settings, string service = "order-management")
    {
        var pool = new DatabasePool(settings);
        var server = new HostBuilder().ConfigureWebHost(web =>
        {
            web.UseTestServer();
            web.ConfigureServices(services =>
            {
                services.AddSingleton(settings);
                services.AddSingleton(pool);
                services.AddSingleton(new JwtTokenService(settings));
                services.AddSingleton(new JwksKeyProvider(new HttpClient(), settings));
                services.AddRouting();
            });
            web.Configure(app =>
            {
                app.UseAgentAuth();
                app.UseRouting();
                app.UseEndpoints(e =>
                {
                    e.MapGet("/health", () => Results.Ok(new { healthy = true }));
                    e.MapReadiness(service);
                });
            });
        }).Start().GetTestServer();
        return server.CreateClient();
    }

    private static async Task<JsonElement> BodyAsync(HttpResponseMessage res) =>
        JsonDocument.Parse(await res.Content.ReadAsStringAsync()).RootElement;

    [Fact]
    public async Task Ready_WhenDatabaseAndSchemaAreUp_AndRedisIsNot()
    {
        // No Authorization header at all: /ready is public like /health. Redis is unreachable
        // on purpose — it is reported but must not make the service not-ready.
        using var client = ClientFor(Settings());

        var res = await client.GetAsync("/ready");

        res.StatusCode.Should().Be(HttpStatusCode.OK);
        var body = await BodyAsync(res);
        body.GetProperty("status").GetString().Should().Be("ready");
        body.GetProperty("service").GetString().Should().Be("order-management");
        var checks = body.GetProperty("checks");
        foreach (var name in new[] { "database", "migrations", "redis" })
        {
            checks.GetProperty(name).EnumerateObject().Select(p => p.Name)
                .Should().BeEquivalentTo(new[] { "ok", "critical", "detail" }, name);
        }
        checks.GetProperty("database").GetProperty("ok").GetBoolean().Should().BeTrue();
        checks.GetProperty("migrations").GetProperty("ok").GetBoolean().Should().BeTrue();
        // Unauthenticated endpoint: ok or not ok only, never the migration version.
        checks.GetProperty("migrations").GetProperty("detail").GetString().Should().Be("ok");
        checks.GetProperty("redis").GetProperty("ok").GetBoolean().Should().BeFalse();
        checks.GetProperty("redis").GetProperty("critical").GetBoolean().Should().BeFalse();
    }

    [Fact]
    public async Task ConcurrentCallers_ShareOneProbe_AndTheResultIsCachedBrieflyThenRefreshed()
    {
        var cache = new ReadinessCache(TimeSpan.FromMilliseconds(300));
        var probes = 0;
        async Task<(int, object)> Probe()
        {
            Interlocked.Increment(ref probes);
            await Task.Delay(150);
            return (200, new { status = "ready" });
        }

        await Task.WhenAll(Enumerable.Range(0, 25).Select(_ => cache.GetAsync(Probe)));
        probes.Should().Be(1, "at most one probe may run at a time, and its result is shared");

        await cache.GetAsync(Probe);
        probes.Should().Be(1, "within the TTL the cached result is reused");

        await Task.Delay(400);
        await cache.GetAsync(Probe);
        probes.Should().Be(2);
    }

    [Fact]
    public async Task NotReady_WhenTheDatabaseIsUnreachable()
    {
        using var client = ClientFor(
            Settings("Host=127.0.0.1;Port=1;Database=x;Username=x;Password=x;Timeout=1"));

        var res = await client.GetAsync("/ready");

        res.StatusCode.Should().Be(HttpStatusCode.ServiceUnavailable);
        var body = await BodyAsync(res);
        body.GetProperty("status").GetString().Should().Be("not_ready");
        body.GetProperty("checks").GetProperty("database").GetProperty("ok").GetBoolean().Should().BeFalse();
    }

    [Fact]
    public async Task NotReady_WhenSchemaMigrationsIsMissing()
    {
        // A database that exists and answers SELECT 1 but was never migrated.
        var name = $"unmigrated_{Guid.NewGuid():N}"[..24];
        await using (var admin = new NpgsqlConnection(pg.ConnectionString))
        {
            await admin.OpenAsync();
            await using var create = new NpgsqlCommand($"CREATE DATABASE \"{name}\"", admin);
            await create.ExecuteNonQueryAsync();
        }
        var url = new NpgsqlConnectionStringBuilder(pg.ConnectionString) { Database = name }.ConnectionString;
        using var client = ClientFor(Settings(url));

        var res = await client.GetAsync("/ready");

        res.StatusCode.Should().Be(HttpStatusCode.ServiceUnavailable);
        var checks = (await BodyAsync(res)).GetProperty("checks");
        checks.GetProperty("database").GetProperty("ok").GetBoolean().Should().BeTrue();
        checks.GetProperty("migrations").GetProperty("ok").GetBoolean().Should().BeFalse();
    }

    /// <summary>
    /// What a paused Postgres container does: the TCP connect succeeds and nothing ever replies.
    /// Probing through the service's pool did not return within 30 s on exactly this.
    /// </summary>
    [Fact]
    public async Task ADatabaseThatAcceptsButNeverAnswers_TimesOutOnItsOwnClock()
    {
        var listener = new System.Net.Sockets.TcpListener(System.Net.IPAddress.Loopback, 0);
        listener.Start();
        var held = new List<System.Net.Sockets.TcpClient>();
        var accept = Task.Run(async () =>
        {
            try { while (true) held.Add(await listener.AcceptTcpClientAsync()); } catch { /* listener stopped */ }
        });
        try
        {
            var port = ((System.Net.IPEndPoint)listener.LocalEndpoint).Port;
            var settings = Settings($"Host=127.0.0.1;Port={port};Database=x;Username=x;Password=x");
            var sw = System.Diagnostics.Stopwatch.StartNew();

            var (status, _) = await ReadinessEndpoint.RunAsync(settings, "x", TimeSpan.FromMilliseconds(700));

            status.Should().Be(503);
            sw.Elapsed.Should().BeLessThan(TimeSpan.FromSeconds(5));
        }
        finally
        {
            listener.Stop();
            await accept;
            held.ForEach(c => c.Dispose());
        }
    }

    [Fact]
    public async Task Health_StaysStaticEvenWhenTheDatabaseIsDown()
    {
        using var client = ClientFor(
            Settings("Host=127.0.0.1;Port=1;Database=x;Username=x;Password=x;Timeout=1"));

        var res = await client.GetAsync("/health");

        res.StatusCode.Should().Be(HttpStatusCode.OK);
    }
}
