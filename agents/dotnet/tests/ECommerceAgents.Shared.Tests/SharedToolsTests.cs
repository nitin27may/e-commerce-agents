using Dapper;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Tools;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using System.Text.Json;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// The shared tool library (#18). Python centralises 23 agent-reachable tools
/// in <c>shared/tools/</c>; .NET had one. These cover the three modules that
/// close the widest gap — <c>get_user_profile</c> alone was missing from all
/// five specialists.
///
/// Run against real Postgres rather than a mock: every one of these is a SQL
/// query, so a mock would assert that the C# calls Dapper, not that the query
/// returns what the model needs.
/// </summary>
[Collection(nameof(LocalPostgresCollection))]
public sealed class SharedToolsTests : IAsyncLifetime
{
    private const string CustomerEmail = "shared.tools@example.com";

    private readonly PostgresFixture _pg;
    private DatabasePool _pool = null!;
    private Guid _userId;
    private Guid _productId;
    private Guid _warehouseId;

    public SharedToolsTests(PostgresFixture pg) => _pg = pg;

    public async Task InitializeAsync()
    {
        var settings = new AgentSettings { DatabaseUrl = _pg.ConnectionString };
        _pool = new DatabasePool(settings);
        await SeedAsync();
    }

    public async Task DisposeAsync() => await _pool.DisposeAsync();

    private async Task SeedAsync()
    {
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            @"TRUNCATE restock_schedule, warehouse_inventory, warehouses, price_history,
                       order_items, orders, products, loyalty_tiers, users
              RESTART IDENTITY CASCADE"
        );

        await conn.ExecuteAsync(
            @"INSERT INTO loyalty_tiers (name, min_spend, discount_pct, free_shipping_threshold, priority_support)
              VALUES ('gold', 3000, 10, 0, TRUE)"
        );

        _userId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO users (email, password_hash, name, role, loyalty_tier, total_spend)
              VALUES (@e, 'x', 'Shared Tools User', 'customer', 'gold', 4200) RETURNING id",
            new { e = CustomerEmail }
        );

        _productId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO products (name, description, category, brand, price)
              VALUES ('Test Headphones', 'desc', 'Electronics', 'Acme', 100.00) RETURNING id"
        );

        _warehouseId = await conn.ExecuteScalarAsync<Guid>(
            "INSERT INTO warehouses (name, location, region) VALUES ('East DC', 'Richmond VA', 'east') RETURNING id"
        );

        await conn.ExecuteAsync(
            @"INSERT INTO warehouse_inventory (warehouse_id, product_id, quantity, reorder_threshold)
              VALUES (@w, @p, 5, 10)",
            new { w = _warehouseId, p = _productId }
        );

        const string addr = "{\"street\":\"1 Test\",\"city\":\"SF\",\"state\":\"CA\",\"zip\":\"94105\",\"country\":\"US\"}";
        var orderId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO orders (user_id, status, total, shipping_address)
              VALUES (@u, 'delivered', 199.99, @a::jsonb) RETURNING id",
            new { u = _userId, a = addr }
        );
        await conn.ExecuteAsync(
            @"INSERT INTO order_items (order_id, product_id, quantity, unit_price, subtotal)
              VALUES (@o, @p, 1, 199.99, 199.99)",
            new { o = orderId, p = _productId }
        );
    }

    private static JsonElement Json(object value) =>
        JsonSerializer.SerializeToElement(value);

    // ─────────────────────── user profile ───────────────────────

    [Fact]
    public async Task GetUserProfile_ReturnsIdentityAndTierBenefits()
    {
        RequestContext.CurrentUserEmail = CustomerEmail;

        var result = Json(await new UserProfileTools(_pool).GetUserProfile());

        result.GetProperty("email").GetString().Should().Be(CustomerEmail);
        result.GetProperty("loyalty_tier").GetString().Should().Be("gold");
        result.GetProperty("total_spend").GetDecimal().Should().Be(4200m);
        result.GetProperty("tier_benefits").GetProperty("discount_pct").GetDecimal().Should().Be(10m);
        result.GetProperty("tier_benefits").GetProperty("priority_support").GetBoolean().Should().BeTrue();
    }

    /// <summary>
    /// Identity comes from ambient context, never a tool argument — so there
    /// is no parameter the model could use to ask about another account.
    /// </summary>
    [Fact]
    public async Task GetUserProfile_WithNoIdentity_RefusesRatherThanGuessing()
    {
        RequestContext.CurrentUserEmail = string.Empty;

        var result = Json(await new UserProfileTools(_pool).GetUserProfile());

        result.GetProperty("error").GetString().Should().Contain("No user context");
    }

    [Fact]
    public async Task GetPurchaseHistory_ReturnsOrdersWithCategories()
    {
        RequestContext.CurrentUserEmail = CustomerEmail;

        var result = Json(await new UserProfileTools(_pool).GetPurchaseHistory());

        result.GetArrayLength().Should().Be(1);
        var order = result[0];
        order.GetProperty("status").GetString().Should().Be("delivered");
        order.GetProperty("total").GetDecimal().Should().Be(199.99m);
        order.GetProperty("categories").EnumerateArray()
            .Select(e => e.GetString()).Should().Contain("Electronics");
    }

    [Fact]
    public async Task GetPurchaseHistory_AnonymousCaller_ReturnsEmptyNotError()
    {
        // "No history" is a usable answer for a recommendation prompt; an
        // error would force the model to apologise instead of carrying on.
        RequestContext.CurrentUserEmail = string.Empty;

        var result = Json(await new UserProfileTools(_pool).GetPurchaseHistory());

        result.GetArrayLength().Should().Be(0);
    }

    // ─────────────────────── stock lookup ───────────────────────

    [Fact]
    public async Task CheckStock_ReportsTotalsAndFlagsLowStock()
    {
        var result = Json(await new StockLookupTools(_pool).CheckStock(_productId.ToString()));

        result.GetProperty("in_stock").GetBoolean().Should().BeTrue();
        result.GetProperty("total_quantity").GetInt32().Should().Be(5);

        var warehouse = result.GetProperty("warehouses")[0];
        warehouse.GetProperty("warehouse").GetString().Should().Be("East DC");
        warehouse.GetProperty("low_stock").GetBoolean()
            .Should().BeTrue("quantity 5 is at or below the reorder threshold of 10");
    }

    [Fact]
    public async Task CheckStock_UnknownProduct_ReportsOutOfStockRatherThanThrowing()
    {
        var result = Json(await new StockLookupTools(_pool).CheckStock(Guid.NewGuid().ToString()));

        result.GetProperty("in_stock").GetBoolean().Should().BeFalse();
        result.GetProperty("total_quantity").GetInt32().Should().Be(0);
    }

    /// <summary>
    /// The model routinely invents ids, so a malformed one must be an answer,
    /// not an exception — see the "dyson-v15-id" case that motivated
    /// FindProductByName.
    /// </summary>
    [Fact]
    public async Task CheckStock_MalformedProductId_IsHandledNotThrown()
    {
        var result = Json(await new StockLookupTools(_pool).CheckStock("not-a-uuid"));

        result.GetProperty("in_stock").GetBoolean().Should().BeFalse();
    }

    [Fact]
    public async Task GetWarehouseAvailability_IncludesUpcomingRestocksOnly()
    {
        await using (var conn = await _pool.OpenAsync())
        {
            await conn.ExecuteAsync(
                @"INSERT INTO restock_schedule (product_id, warehouse_id, expected_quantity, expected_date)
                  VALUES (@p, @w, 50, CURRENT_DATE + 7), (@p, @w, 25, CURRENT_DATE - 7)",
                new { p = _productId, w = _warehouseId }
            );
        }

        var result = Json(await new StockLookupTools(_pool).GetWarehouseAvailability(_productId.ToString()));

        result.GetProperty("warehouses").GetArrayLength().Should().Be(1);
        result.GetProperty("upcoming_restocks").GetArrayLength()
            .Should().Be(1, "a restock already in the past is not upcoming");
        result.GetProperty("upcoming_restocks")[0].GetProperty("expected_quantity").GetInt32().Should().Be(50);
    }

    // ─────────────────────── price history ───────────────────────

    [Fact]
    public async Task GetPriceHistory_NoHistory_SaysSoRatherThanReturningZeroes()
    {
        var result = Json(await new PriceHistoryTools(_pool).GetPriceHistory(_productId.ToString()));

        result.GetProperty("summary").GetString().Should().Contain("No price history");
    }

    [Fact]
    public async Task GetPriceHistory_FallingPrices_ReportsADecreasingTrendAndAGoodDeal()
    {
        await using (var conn = await _pool.OpenAsync())
        {
            // 14 points: a high leading week and a low trailing week, so the
            // 5% deadband is cleared in the "decreasing" direction.
            for (var day = 14; day >= 1; day--)
            {
                var price = day > 7 ? 200m : 110m;
                await conn.ExecuteAsync(
                    "INSERT INTO price_history (product_id, price, recorded_at) VALUES (@p, @price, NOW() - (@d || ' days')::interval)",
                    new { p = _productId, price, d = day.ToString() }
                );
            }
        }

        var result = Json(await new PriceHistoryTools(_pool).GetPriceHistory(_productId.ToString()));

        result.GetProperty("trend").GetString().Should().Be("decreasing");
        result.GetProperty("data_points").GetInt32().Should().Be(14);
        result.GetProperty("is_good_deal").GetBoolean()
            .Should().BeTrue("the current price of 100 is well below the period average");
    }

    [Fact]
    public async Task GetPriceHistory_TooFewPoints_SaysInsufficientRatherThanGuessing()
    {
        await using (var conn = await _pool.OpenAsync())
        {
            await conn.ExecuteAsync(
                "INSERT INTO price_history (product_id, price, recorded_at) VALUES (@p, 150, NOW() - INTERVAL '1 day')",
                new { p = _productId }
            );
        }

        var result = Json(await new PriceHistoryTools(_pool).GetPriceHistory(_productId.ToString()));

        result.GetProperty("trend").GetString().Should().Be("insufficient_data");
    }

    [Fact]
    public async Task GetPriceHistory_UnknownProduct_ReturnsAnError()
    {
        var result = Json(await new PriceHistoryTools(_pool).GetPriceHistory(Guid.NewGuid().ToString()));

        result.GetProperty("error").GetString().Should().Contain("Product not found");
    }
}
