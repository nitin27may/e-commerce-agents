using Dapper;
using ECommerceAgents.OrderManagement.Tools;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using Xunit;

namespace ECommerceAgents.OrderManagement.Tests;

[CollectionDefinition(nameof(LocalPostgresCollection))]
public sealed class LocalPostgresCollection : ICollectionFixture<PostgresFixture> { }

/// <summary>
/// Per-tool tests against a real Postgres testcontainer (matches the
/// Python pattern: never mock the DB — we want schema bugs to surface
/// here, not in production). Each test seeds its own user + order set
/// and tears down after.
/// </summary>
[Collection(nameof(LocalPostgresCollection))]
public sealed class OrderToolsTests : IAsyncLifetime
{
    private readonly PostgresFixture _pg;
    private DatabasePool _pool = null!;
    private OrderTools _tools = null!;
    private const string Email = "tester@example.com";
    private Guid _userId;

    public OrderToolsTests(PostgresFixture pg)
    {
        _pg = pg;
        // Set the AsyncLocal in the constructor so it lives in the same
        // sync context xUnit will run the test method from. Setting it
        // inside InitializeAsync sometimes flowed away once the
        // initialise task completed.
        RequestContext.CurrentUserEmail = Email;
    }

    public async Task InitializeAsync()
    {
        var settings = new AgentSettings { DatabaseUrl = _pg.ConnectionString };
        _pool = new DatabasePool(settings);
        _tools = new OrderTools(_pool);
        RequestContext.CurrentUserEmail = Email;
        await SeedAsync();
    }

    public async Task DisposeAsync()
    {
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            "TRUNCATE order_status_history, order_items, orders, products, users RESTART IDENTITY CASCADE"
        );
        RequestContext.CurrentUserEmail = "";
        await _pool.DisposeAsync();
    }

    private void EnsureUserScope()
    {
        // Defensive: each test re-asserts the AsyncLocal in case xUnit
        // hopped contexts between InitializeAsync and the test body.
        RequestContext.CurrentUserEmail = Email;
    }

    // ─────────────────────── Read-only ───────────────────────

    [Fact]
    public async Task GetUserOrders_ReturnsScopedToCurrentEmail()
    {
        EnsureUserScope();
        var orders = await _tools.GetUserOrders();
        orders.Should().HaveCount(2);
        orders.Should().OnlyContain(o => o.Total > 0);
    }

    [Fact]
    public async Task GetUserOrders_FiltersByStatus()
    {
        EnsureUserScope();
        var placed = await _tools.GetUserOrders(status: "placed");
        var delivered = await _tools.GetUserOrders(status: "delivered");
        placed.Should().HaveCount(1);
        delivered.Should().HaveCount(1);
    }

    [Theory]
    [InlineData(0, 1)]
    [InlineData(50, 50)]
    [InlineData(int.MaxValue, 100)]
    public async Task GetUserOrders_ClampsLimit(int requested, int expectedMax)
    {
        EnsureUserScope();
        var rows = await _tools.GetUserOrders(limit: requested);
        rows.Count.Should().BeLessThanOrEqualTo(expectedMax);
    }

    [Fact]
    public async Task GetUserOrders_EmptyEmailReturnsNothing()
    {
        EnsureUserScope();
        RequestContext.CurrentUserEmail = "";
        var rows = await _tools.GetUserOrders();
        rows.Should().BeEmpty();
    }

    [Fact]
    public async Task GetOrderDetails_RejectsCrossUserAccess()
    {
        EnsureUserScope();
        var (orderId, _) = await SeedOtherUserOrderAsync();
        var details = await _tools.GetOrderDetails(orderId.ToString());
        details.Should().BeNull();
    }

    [Fact]
    public async Task GetOrderDetails_ReturnsNullForBadGuid()
    {
        EnsureUserScope();
        var details = await _tools.GetOrderDetails("not-a-uuid");
        details.Should().BeNull();
    }

    [Fact]
    public async Task GetOrderTracking_NoTrackingForFreshOrder()
    {
        EnsureUserScope();
        var orderId = await GetFirstOrderIdByStatus("placed");
        var tracking = await _tools.GetOrderTracking(orderId.ToString());
        tracking.Should().NotBeNull();
        tracking!.Message.Should().Contain("not shipped");
        tracking.LatestUpdate.Should().BeNull();
    }

    [Fact]
    public async Task GetOrderTracking_ReturnsTimelineForDeliveredOrder()
    {
        EnsureUserScope();
        var orderId = await GetFirstOrderIdByStatus("delivered");
        await using (var conn = await _pool.OpenAsync())
        {
            await conn.ExecuteAsync(
                @"INSERT INTO order_status_history (order_id, status, notes, location, timestamp)
                  VALUES (@id, 'shipped', 'Out for delivery', 'SFO', NOW() - INTERVAL '1 hour'),
                         (@id, 'delivered', 'Signed by customer', 'SF', NOW())",
                new { id = orderId }
            );
        }

        var tracking = await _tools.GetOrderTracking(orderId.ToString());
        tracking.Should().NotBeNull();
        tracking!.Timeline.Should().HaveCount(2);
        // Timeline ascending; latest_update was queried separately by DESC.
        tracking.LatestUpdate!.Status.Should().Be("delivered");
        tracking.Timeline[0].Status.Should().Be("shipped");
        tracking.Timeline[1].Status.Should().Be("delivered");
    }

    // ─────────────────────── State-mutating ──────────────────

    [Fact]
    public async Task CancelOrder_RejectsBadOrderId()
    {
        EnsureUserScope();
        var result = await _tools.CancelOrder("not-a-uuid", "no longer needed");
        result.Error.Should().Contain("UUID");
    }

    [Fact]
    public async Task CancelOrder_RejectsEmptyReason()
    {
        EnsureUserScope();
        var orderId = await GetFirstOrderIdByStatus("placed");
        var result = await _tools.CancelOrder(orderId.ToString(), "");
        result.Error.Should().Contain("1-500 characters");
    }

    [Fact]
    public async Task CancelOrder_RefusesToCancelDeliveredOrder()
    {
        EnsureUserScope();
        var orderId = await GetFirstOrderIdByStatus("delivered");
        var result = await _tools.CancelOrder(orderId.ToString(), "changed my mind");
        result.Error.Should().Contain("delivered");
        result.NewStatus.Should().BeNull();
    }

    [Fact]
    public async Task CancelOrder_HappyPath_TransitionsStatusAndWritesHistory()
    {
        EnsureUserScope();
        var orderId = await GetFirstOrderIdByStatus("placed");
        var result = await _tools.CancelOrder(orderId.ToString(), "ordered the wrong size");
        result.Error.Should().BeNull();
        result.NewStatus.Should().Be("cancelled");
        result.PreviousStatus.Should().Be("placed");
        result.RefundAmount.Should().BeGreaterThan(0);

        await using var conn = await _pool.OpenAsync();
        var dbStatus = await conn.ExecuteScalarAsync<string>(
            "SELECT status FROM orders WHERE id = @id",
            new { id = orderId }
        );
        dbStatus.Should().Be("cancelled");
        var historyCount = await conn.ExecuteScalarAsync<int>(
            "SELECT COUNT(*) FROM order_status_history WHERE order_id = @id AND status = 'cancelled'",
            new { id = orderId }
        );
        historyCount.Should().Be(1);
    }

    [Fact]
    public async Task ModifyOrder_RejectsBadAddress()
    {
        EnsureUserScope();
        var orderId = await GetFirstOrderIdByStatus("placed");
        var bad = new ShippingAddressInput("123 St", "SF", "California", "AAAA", "United States");
        var result = await _tools.ModifyOrder(orderId.ToString(), bad);
        result.Error.Should().NotBeNull();
        result.Message.Should().BeNull();
    }

    [Fact]
    public async Task ModifyOrder_HappyPath_UpdatesShippingAddress()
    {
        EnsureUserScope();
        var orderId = await GetFirstOrderIdByStatus("placed");
        var addr = new ShippingAddressInput("221B Baker St", "London", "LD", "NW1 6XE", "GB");

        var result = await _tools.ModifyOrder(orderId.ToString(), addr);
        result.Error.Should().BeNull();

        await using var conn = await _pool.OpenAsync();
        var json = await conn.ExecuteScalarAsync<string>(
            "SELECT shipping_address::text FROM orders WHERE id = @id",
            new { id = orderId }
        );
        json.Should().Contain("Baker St").And.Contain("London");
    }

    [Fact]
    public async Task ModifyOrder_RefusesShippedOrder()
    {
        EnsureUserScope();
        await using (var conn = await _pool.OpenAsync())
        {
            await conn.ExecuteAsync(
                "UPDATE orders SET status = 'shipped' WHERE user_id = @uid AND status = 'placed'",
                new { uid = _userId }
            );
        }

        // After the update no 'placed' rows exist; pick the now-shipped one.
        Guid orderId;
        await using (var conn = await _pool.OpenAsync())
        {
            orderId = await conn.ExecuteScalarAsync<Guid>(
                "SELECT id FROM orders WHERE user_id = @uid AND status = 'shipped' LIMIT 1",
                new { uid = _userId }
            );
        }

        var addr = new ShippingAddressInput("1 Loop", "Cupertino", "CA", "95014", "US");
        var result = await _tools.ModifyOrder(orderId.ToString(), addr);
        result.Error.Should().Contain("shipped");
    }

    // ─────────────────────── Seed helpers ────────────────────

    private async Task SeedAsync()
    {
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            "TRUNCATE order_status_history, order_items, orders, products, users RESTART IDENTITY CASCADE"
        );

        _userId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO users (email, password_hash, name, role)
              VALUES (@email, 'x', 'Tester', 'customer')
              RETURNING id",
            new { email = Email }
        );

        var prodId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO products (name, description, category, brand, price)
              VALUES ('Sample', 'Sample', 'Electronics', 'X', 19.99)
              RETURNING id"
        );

        // Two orders for our user — one 'placed', one 'delivered'.
        const string addr = "{\"street\":\"100 Test\",\"city\":\"SF\",\"state\":\"CA\",\"zip\":\"94105\",\"country\":\"US\"}";
        var placedId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO orders (user_id, status, total, shipping_address)
              VALUES (@uid, 'placed', 19.99, @addr::jsonb) RETURNING id",
            new { uid = _userId, addr }
        );
        var deliveredId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO orders (user_id, status, total, shipping_address)
              VALUES (@uid, 'delivered', 39.98, @addr::jsonb) RETURNING id",
            new { uid = _userId, addr }
        );

        await conn.ExecuteAsync(
            @"INSERT INTO order_items (order_id, product_id, quantity, unit_price, subtotal)
              VALUES (@p, @prod, 1, 19.99, 19.99),
                     (@d, @prod, 2, 19.99, 39.98)",
            new { p = placedId, d = deliveredId, prod = prodId }
        );
    }

    private async Task<(Guid orderId, Guid userId)> SeedOtherUserOrderAsync()
    {
        await using var conn = await _pool.OpenAsync();
        var otherId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO users (email, password_hash, name, role)
              VALUES ('other@example.com', 'x', 'Other', 'customer')
              RETURNING id"
        );
        const string addr = "{\"street\":\"1 Other\",\"city\":\"NYC\",\"state\":\"NY\",\"zip\":\"10001\",\"country\":\"US\"}";
        var orderId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO orders (user_id, status, total, shipping_address)
              VALUES (@uid, 'placed', 9.99, @addr::jsonb) RETURNING id",
            new { uid = otherId, addr }
        );
        return (orderId, otherId);
    }

    private async Task<Guid> GetFirstOrderIdByStatus(string status)
    {
        await using var conn = await _pool.OpenAsync();
        return await conn.ExecuteScalarAsync<Guid>(
            "SELECT id FROM orders WHERE user_id = @uid AND status = @status LIMIT 1",
            new { uid = _userId, status }
        );
    }
}
