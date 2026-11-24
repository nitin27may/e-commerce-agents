using Dapper;
using ECommerceAgents.Orchestrator.Routes;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using Microsoft.Extensions.DependencyInjection;
using System.Collections.Concurrent;
using System.Net;
using Xunit;

namespace ECommerceAgents.Orchestrator.Tests;

/// <summary>SEC-1 — the public label route accepts only a full lowercase hex token (12 legacy or 32 new), no LIKE wildcards.</summary>
public sealed class ReturnLabelTokenTests
{
    [Theory]
    [InlineData("a1b2c3d4e5f6")]
    [InlineData("000000000000")]
    [InlineData("0123456789abcdef0123456789abcdef")]
    public void WellFormed_AcceptsTheLegacyAndTheNewFormat(string token) =>
        ReturnLabelRoutes.IsWellFormedToken(token).Should().BeTrue();

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("%")]
    [InlineData("_")]
    [InlineData("____________")]
    [InlineData("%b2c3d4e5f6")]
    [InlineData("a1b2c3")]
    [InlineData("a1b2c3d4e5f6a")]
    [InlineData("A1B2C3D4E5F6")]
    [InlineData("a1b2c3d4e5f%")]
    [InlineData("0123456789abcdef0123456789abcde")]
    [InlineData("0123456789abcdef0123456789abcdef0")]
    [InlineData("0123456789ABCDEF0123456789ABCDEF")]
    [InlineData("%123456789abcdef0123456789abcdef")]
    public void WellFormed_RefusesEverythingElse(string? token) =>
        ReturnLabelRoutes.IsWellFormedToken(token).Should().BeFalse();

    [Fact]
    public void New_MintsDistinct128BitLowercaseHex()
    {
        var tokens = Enumerable.Range(0, 200).Select(_ => ReturnLabelToken.New()).ToList();
        tokens.Distinct().Should().HaveCount(200);
        tokens.Should().OnlyContain(t => t.Length == 32 && ReturnLabelToken.IsWellFormed(t) && !ReturnLabelToken.IsLegacy(t));
    }

    [Fact]
    public void LegacyExpired_IsTrueOnlyPastThirtyDays()
    {
        var now = new DateTime(2026, 6, 30, 0, 0, 0, DateTimeKind.Utc);
        ReturnLabelToken.LegacyExpired(now.AddDays(-29), now).Should().BeFalse();
        ReturnLabelToken.LegacyExpired(now.AddDays(-31), now).Should().BeTrue();
        ReturnLabelToken.LegacyExpired(null, now).Should().BeTrue();
    }
}

/// <summary>The label route: new and legacy tokens, legacy expiry, and the unknown-token budget (an in-memory gate, no Redis).</summary>
[Collection(nameof(LocalPostgresCollection))]
public sealed class ReturnLabelRouteTests(PostgresFixture pg) : IAsyncLifetime
{
    private const string NewToken = "0123456789abcdef0123456789abcdef";
    private const string LegacyToken = "a1b2c3d4e5f6";
    private DatabasePool _pool = null!;
    private Guid _returnId;

    public async Task InitializeAsync()
    {
        _pool = new DatabasePool(new AgentSettings { DatabaseUrl = pg.ConnectionString });
        await using var conn = await _pool.OpenAsync();
        var userId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO users (email, password_hash, name, role) VALUES ('label@example.com', 'x', 'Label', 'customer') RETURNING id");
        var orderId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO orders (user_id, status, total, shipping_address)
              VALUES (@u, 'delivered', 25, '{""street"":""1 St"",""city"":""X"",""state"":""Y"",""zip"":""1""}'::jsonb) RETURNING id",
            new { u = userId });
        _returnId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO returns (order_id, user_id, reason, status, return_label_url)
              VALUES (@o, @u, 'damaged', 'requested', '/api/returns/placeholder/label') RETURNING id",
            new { o = orderId, u = userId });
    }

    public async Task DisposeAsync()
    {
        await using (var conn = await _pool.OpenAsync())
        {
            await conn.ExecuteAsync("TRUNCATE returns, order_items, orders, users RESTART IDENTITY CASCADE");
        }

        await _pool.DisposeAsync();
    }

    private async Task UseTokenAsync(string token, int ageDays = 0)
    {
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            "UPDATE returns SET return_label_url = @url, created_at = NOW() - make_interval(days => @age) WHERE id = @id",
            new { url = $"/api/returns/{token}/label", age = ageDays, id = _returnId });
    }

    private HttpClient Client(ILabelLookupGate? gate = null) =>
        OrchestratorTestHost
            .Create(_pool, r => r.MapReturnLabelRoutes(), configureServices: s =>
            {
                if (gate is not null) s.AddSingleton(gate);
            })
            .CreateClient();

    [Fact]
    public async Task ANewToken_ServesItsLabel()
    {
        await UseTokenAsync(NewToken);
        using var client = Client();
        (await client.GetAsync($"/api/returns/{NewToken}/label")).StatusCode.Should().Be(HttpStatusCode.OK);
    }

    [Fact]
    public async Task ALegacyToken_WorksUntilItExpires()
    {
        await UseTokenAsync(LegacyToken);
        using var client = Client();
        (await client.GetAsync($"/api/returns/{LegacyToken}/label")).StatusCode.Should().Be(HttpStatusCode.OK);

        await UseTokenAsync(LegacyToken, ageDays: 31);
        (await client.GetAsync($"/api/returns/{LegacyToken}/label")).StatusCode.Should().Be(HttpStatusCode.NotFound);
    }

    [Fact]
    public async Task AnOldNewStyleToken_DoesNotExpire()
    {
        await UseTokenAsync(NewToken, ageDays: 400);
        using var client = Client();
        (await client.GetAsync($"/api/returns/{NewToken}/label")).StatusCode.Should().Be(HttpStatusCode.OK);
    }

    [Fact]
    public async Task UnknownAndMalformedTokens_SpendTheBudgetThenAreRefusedUpFront()
    {
        await UseTokenAsync(NewToken);
        var gate = new CountingGate(budget: 3);
        using var client = Client(gate);

        (await client.GetAsync($"/api/returns/{new string('f', 32)}/label")).StatusCode.Should().Be(HttpStatusCode.NotFound);
        (await client.GetAsync("/api/returns/%25/label")).StatusCode.Should().Be(HttpStatusCode.NotFound);
        (await client.GetAsync($"/api/returns/{new string('e', 32)}/label")).StatusCode.Should().Be(HttpStatusCode.NotFound);

        // Budget spent: even the valid token is refused before the lookup.
        (await client.GetAsync($"/api/returns/{NewToken}/label")).StatusCode.Should().Be(HttpStatusCode.TooManyRequests);
    }

    [Fact]
    public async Task ValidLookups_NeverSpendTheBudget()
    {
        await UseTokenAsync(NewToken);
        var gate = new CountingGate(budget: 2);
        using var client = Client(gate);

        for (var i = 0; i < 6; i++)
        {
            (await client.GetAsync($"/api/returns/{NewToken}/label")).StatusCode.Should().Be(HttpStatusCode.OK);
        }

        gate.Misses.Should().Be(0);
    }

    private sealed class CountingGate(int budget) : ILabelLookupGate
    {
        private int _misses;

        public int Misses => _misses;

        public Task<bool> IsBlockedAsync(string? clientIp, CancellationToken ct = default) =>
            Task.FromResult(Volatile.Read(ref _misses) >= budget);

        public Task RecordMissAsync(string? clientIp, CancellationToken ct = default)
        {
            Interlocked.Increment(ref _misses);
            return Task.CompletedTask;
        }
    }
}
