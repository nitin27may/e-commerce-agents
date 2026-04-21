using Dapper;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.ContextProviders;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

[CollectionDefinition(nameof(LocalPostgresCollection))]
public sealed class LocalPostgresCollection : ICollectionFixture<PostgresFixture> { }

/// <summary>
/// Parity tests for <see cref="ContextEnricher"/> — the .NET twin of
/// Python's <c>ECommerceContextProvider</c>. Asserts that for the same
/// DB state the enriched <c>UserContext</c> text contains the same
/// user / orders / memories sections.
/// </summary>
[Collection(nameof(LocalPostgresCollection))]
public sealed class ContextEnricherTests : IAsyncLifetime
{
    private readonly PostgresFixture _pg;
    private DatabasePool _pool = null!;
    private ContextEnricher _enricher = null!;
    private const string Email = "tester@example.com";
    private Guid _userId;

    public ContextEnricherTests(PostgresFixture pg) => _pg = pg;

    public async Task InitializeAsync()
    {
        var settings = new AgentSettings { DatabaseUrl = _pg.ConnectionString };
        _pool = new DatabasePool(settings);
        _enricher = new ContextEnricher(_pool);
        await SeedAsync();
    }

    public async Task DisposeAsync()
    {
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            "TRUNCATE agent_memories, order_items, orders, products, users RESTART IDENTITY CASCADE"
        );
        await _pool.DisposeAsync();
    }

    [Fact]
    public async Task EnrichAsync_EmptyForUnknownUser()
    {
        var result = await _enricher.EnrichAsync("ghost@example.com");
        result.Should().Be(EnrichedContext.Empty);
        result.UserContext.Should().BeEmpty();
    }

    [Fact]
    public async Task EnrichAsync_EmptyForSystemEmail()
    {
        var result = await _enricher.EnrichAsync("system");
        result.Should().Be(EnrichedContext.Empty);
    }

    [Fact]
    public async Task EnrichAsync_IncludesProfileAndOrdersAndMemories()
    {
        var result = await _enricher.EnrichAsync(Email);
        result.Profile.Should().NotBeNull();
        result.Profile!.Name.Should().Be("Tester");
        result.RecentOrders.Should().HaveCountGreaterThanOrEqualTo(2);
        result.Memories.Should().HaveCountGreaterThanOrEqualTo(1);

        result.UserContext.Should().Contain("Current user:");
        result.UserContext.Should().Contain("Recent orders");
        result.UserContext.Should().Contain("User Preferences");
        // Assert the first 8 chars of an order id appear as the short-form label.
        result.UserContext.Should().Contain(result.RecentOrders[0].Id[..8]);
    }

    [Fact]
    public async Task EnrichAsync_RespectsRecentOrdersLimit()
    {
        var tightEnricher = new ContextEnricher(_pool) { RecentOrdersLimit = 1 };
        var result = await tightEnricher.EnrichAsync(Email);
        result.RecentOrders.Should().HaveCount(1);
    }

    [Fact]
    public async Task EnrichAsync_SkipsInactiveAndExpiredMemories()
    {
        var result = await _enricher.EnrichAsync(Email);
        result.Memories.Should().NotContain(m => m.Content.Contains("INACTIVE"));
        result.Memories.Should().NotContain(m => m.Content.Contains("EXPIRED"));
    }

    // ─────────────────────── seed ───────────────────────

    private async Task SeedAsync()
    {
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            "TRUNCATE agent_memories, order_items, orders, products, users RESTART IDENTITY CASCADE"
        );

        _userId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO users (email, password_hash, name, role, loyalty_tier, total_spend)
              VALUES (@email, 'x', 'Tester', 'customer', 'gold', 2500.00)
              RETURNING id",
            new { email = Email }
        );

        const string addr = "{\"street\":\"100 Test\",\"city\":\"SF\",\"state\":\"CA\",\"zip\":\"94105\",\"country\":\"US\"}";
        await conn.ExecuteAsync(
            @"INSERT INTO orders (user_id, status, total, shipping_address, created_at)
              VALUES (@uid, 'placed',    19.99,  @addr::jsonb, NOW() - INTERVAL '5 days'),
                     (@uid, 'delivered', 39.98,  @addr::jsonb, NOW() - INTERVAL '10 days'),
                     (@uid, 'delivered', 129.00, @addr::jsonb, NOW() - INTERVAL '30 days')",
            new { uid = _userId, addr }
        );

        await conn.ExecuteAsync(
            @"INSERT INTO agent_memories (user_id, category, content, importance, is_active, expires_at)
              VALUES
                (@uid, 'preference', 'Prefers wireless headphones',              8, TRUE,  NULL),
                (@uid, 'preference', 'Prefers express shipping',                 6, TRUE,  NOW() + INTERVAL '30 days'),
                (@uid, 'preference', 'INACTIVE entry — should not surface',      9, FALSE, NULL),
                (@uid, 'preference', 'EXPIRED entry — should not surface',       9, TRUE,  NOW() - INTERVAL '1 day')",
            new { uid = _userId }
        );
    }
}
