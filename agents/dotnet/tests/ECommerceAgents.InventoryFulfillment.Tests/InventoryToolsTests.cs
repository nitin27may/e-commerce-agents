using Dapper;
using ECommerceAgents.InventoryFulfillment.Tools;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using Xunit;

namespace ECommerceAgents.InventoryFulfillment.Tests;

[CollectionDefinition(nameof(LocalPostgresCollection))]
public sealed class LocalPostgresCollection : ICollectionFixture<PostgresFixture> { }

[Collection(nameof(LocalPostgresCollection))]
public sealed class InventoryToolsTests : IAsyncLifetime
{
    private readonly PostgresFixture _pg;
    private DatabasePool _pool = null!;
    private InventoryTools _tools = null!;
    private const string Email = "tester@example.com";

    private Guid _productId;
    private Guid _orderShippedId;
    private Guid _orderPlacedId;

    public InventoryToolsTests(PostgresFixture pg)
    {
        _pg = pg;
        RequestContext.CurrentUserEmail = Email;
    }

    public async Task InitializeAsync()
    {
        var settings = new AgentSettings { DatabaseUrl = _pg.ConnectionString };
        _pool = new DatabasePool(settings);
        _tools = new InventoryTools(_pool);
        RequestContext.CurrentUserEmail = Email;
        await SeedAsync();
    }

    public async Task DisposeAsync()
    {
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            @"TRUNCATE order_status_history, order_items, orders, restock_schedule,
                       warehouse_inventory, shipping_rates, carriers, warehouses,
                       products, users RESTART IDENTITY CASCADE"
        );
        RequestContext.CurrentUserEmail = "";
        await _pool.DisposeAsync();
    }

    private void EnsureUserScope() => RequestContext.CurrentUserEmail = Email;

    // ─────────────────────── get_restock_schedule ────────────

    [Fact]
    public async Task GetRestockSchedule_RejectsBadGuid()
    {
        var result = await _tools.GetRestockSchedule("not-a-uuid");
        result.Error.Should().Contain("not found");
    }

    [Fact]
    public async Task GetRestockSchedule_ReturnsUpcomingDatesOnly()
    {
        var result = await _tools.GetRestockSchedule(_productId.ToString());
        result.Error.Should().BeNull();
        result.UpcomingRestocks!.Should().HaveCount(2);
        result.NextRestock.Should().NotBeNull();
        // Past-dated row from seed should NOT appear.
        result.UpcomingRestocks!.Should().NotContain(r => r.ExpectedDate.StartsWith("2000-"));
    }

    // ─────────────────────── estimate_shipping ───────────────

    [Fact]
    public async Task EstimateShipping_ReturnsOptionsWhenStockAvailable()
    {
        var result = await _tools.EstimateShipping(_productId.ToString(), "east");
        result.Available.Should().BeTrue();
        result.ShipsFrom!.Region.Should().Be("east"); // prefer same region
        result.ShippingOptions.Should().NotBeEmpty();
        result.ShippingOptions!.Should().BeInAscendingOrder(o => o.Price);
    }

    [Fact]
    public async Task EstimateShipping_FallsBackToOtherRegionWhenSameRegionEmpty()
    {
        // 'central' has no warehouse with stock for our product; falls back
        // to whichever region has inventory (east).
        var result = await _tools.EstimateShipping(_productId.ToString(), "central");
        result.Available.Should().BeTrue();
        result.ShipsFrom!.Region.Should().Be("east");
    }

    [Fact]
    public async Task EstimateShipping_ReportsUnavailableWhenAllOutOfStock()
    {
        await using (var conn = await _pool.OpenAsync())
        {
            await conn.ExecuteAsync("UPDATE warehouse_inventory SET quantity = 0");
        }
        var result = await _tools.EstimateShipping(_productId.ToString(), "east");
        result.Available.Should().BeFalse();
        result.Message.Should().Contain("out of stock");
    }

    // ─────────────────────── compare_carriers ────────────────

    [Fact]
    public async Task CompareCarriers_PicksCheapestAndFastest()
    {
        var result = await _tools.CompareCarriers("east", "west");
        result.Carriers.Should().HaveCount(3);
        result.BestValue.Should().Be("Standard");
        result.Fastest.Should().Be("Overnight");
    }

    [Fact]
    public async Task CompareCarriers_NoRouteReturnsEmpty()
    {
        var result = await _tools.CompareCarriers("east", "antarctica");
        result.Carriers.Should().BeEmpty();
        result.Message.Should().Contain("No shipping rates");
    }

    // ─────────────────────── get_tracking_status ─────────────

    [Fact]
    public async Task GetTrackingStatus_RequiresUserContext()
    {
        EnsureUserScope();
        RequestContext.CurrentUserEmail = "";
        var result = await _tools.GetTrackingStatus(_orderShippedId.ToString());
        result.Error.Should().Contain("user context");
    }

    [Fact]
    public async Task GetTrackingStatus_RejectsBadGuid()
    {
        EnsureUserScope();
        var result = await _tools.GetTrackingStatus("not-a-uuid");
        result.Error.Should().NotBeNull();
    }

    [Fact]
    public async Task GetTrackingStatus_ReturnsHistoryForShippedOrder()
    {
        EnsureUserScope();
        var result = await _tools.GetTrackingStatus(_orderShippedId.ToString());
        result.Error.Should().BeNull();
        result.History.Should().NotBeNull();
        result.LatestUpdate.Should().NotBeNull();
        result.Status.Should().Be("shipped");
    }

    [Fact]
    public async Task GetTrackingStatus_NotShippedYieldsHumanMessage()
    {
        EnsureUserScope();
        var result = await _tools.GetTrackingStatus(_orderPlacedId.ToString());
        result.Error.Should().BeNull();
        result.Message.Should().Contain("once shipped");
        result.LatestUpdate.Should().BeNull();
    }

    // ─────────────────────── calculate_fulfillment_plan ─────

    [Fact]
    public async Task CalculateFulfillmentPlan_RejectsEmptyList()
    {
        var result = await _tools.CalculateFulfillmentPlan([], "east");
        result.Error.Should().Contain("No product IDs");
    }

    [Fact]
    public async Task CalculateFulfillmentPlan_ReportsUnavailableProducts()
    {
        var unknown = Guid.NewGuid().ToString();
        var result = await _tools.CalculateFulfillmentPlan(
            [_productId.ToString(), unknown, "not-a-uuid"],
            "east"
        );
        result.AllAvailable.Should().BeFalse();
        result.UnavailableProducts.Should().Contain(unknown);
        result.UnavailableProducts.Should().Contain("not-a-uuid");
        result.TotalItems.Should().Be(1);
    }

    [Fact]
    public async Task CalculateFulfillmentPlan_BuildsSingleShipmentPerWarehouse()
    {
        var result = await _tools.CalculateFulfillmentPlan([_productId.ToString()], "east");
        result.Error.Should().BeNull();
        result.TotalShipments.Should().Be(1);
        result.TotalShippingCost.Should().BeGreaterThan(0m);
        result.Shipments.Should().HaveCount(1);
    }

    // ─────────────────────── place_backorder ─────────────────

    [Fact]
    public async Task PlaceBackorder_RequiresUserContext()
    {
        EnsureUserScope();
        RequestContext.CurrentUserEmail = "";
        var result = await _tools.PlaceBackorder(_productId.ToString(), 1);
        result.Error.Should().Contain("user context");
    }

    [Fact]
    public async Task PlaceBackorder_RejectsNonPositiveQuantity()
    {
        EnsureUserScope();
        var result = await _tools.PlaceBackorder(_productId.ToString(), 0);
        result.Error.Should().Contain("greater than zero");
    }

    [Fact]
    public async Task PlaceBackorder_RefusesWhenStockAvailable()
    {
        EnsureUserScope();
        var result = await _tools.PlaceBackorder(_productId.ToString(), 1);
        result.BackorderPlaced.Should().BeFalse();
        result.Message.Should().Contain("in stock");
        result.CurrentStock.Should().BeGreaterThan(0);
    }

    [Fact]
    public async Task PlaceBackorder_CreatesBackorderWhenOutOfStock()
    {
        EnsureUserScope();
        await using (var conn = await _pool.OpenAsync())
        {
            await conn.ExecuteAsync("UPDATE warehouse_inventory SET quantity = 0");
        }
        var result = await _tools.PlaceBackorder(_productId.ToString(), 3);
        result.BackorderPlaced.Should().BeTrue();
        result.BackorderId.Should().NotBeNullOrEmpty();
        result.Quantity.Should().Be(3);
        result.ExpectedRestock.Should().NotBeNull();
    }

    // ─────────────────────── seed ────────────────────────────

    private async Task SeedAsync()
    {
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            @"TRUNCATE order_status_history, order_items, orders, restock_schedule,
                       warehouse_inventory, shipping_rates, carriers, warehouses,
                       products, users RESTART IDENTITY CASCADE"
        );

        var userId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO users (email, password_hash, name, role)
              VALUES (@email, 'x', 'Tester', 'customer')
              RETURNING id",
            new { email = Email }
        );
        _productId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO products (name, description, category, brand, price)
              VALUES ('Headphones', 'Sample', 'Electronics', 'X', 200)
              RETURNING id"
        );

        var eastWh = await conn.ExecuteScalarAsync<Guid>(
            "INSERT INTO warehouses (name, location, region) VALUES ('East', 'Richmond, VA', 'east') RETURNING id"
        );
        var westWh = await conn.ExecuteScalarAsync<Guid>(
            "INSERT INTO warehouses (name, location, region) VALUES ('West', 'San Jose, CA', 'west') RETURNING id"
        );

        await conn.ExecuteAsync(
            @"INSERT INTO warehouse_inventory (warehouse_id, product_id, quantity)
              VALUES (@east, @pid, 12), (@west, @pid, 5)",
            new { east = eastWh, west = westWh, pid = _productId }
        );

        // Restock: one past-dated (must NOT appear), two future.
        await conn.ExecuteAsync(
            @"INSERT INTO restock_schedule (product_id, warehouse_id, expected_quantity, expected_date)
              VALUES (@pid, @east, 50, DATE '2000-01-01'),
                     (@pid, @east, 30, CURRENT_DATE + INTERVAL '7 days'),
                     (@pid, @west, 20, CURRENT_DATE + INTERVAL '14 days')",
            new { pid = _productId, east = eastWh, west = westWh }
        );

        // Three carriers across one route (east → west). Standard cheapest,
        // Overnight fastest.
        var standardId = await conn.ExecuteScalarAsync<Guid>(
            "INSERT INTO carriers (name, speed_tier, base_rate) VALUES ('Standard', 'standard', 5) RETURNING id"
        );
        var expressId = await conn.ExecuteScalarAsync<Guid>(
            "INSERT INTO carriers (name, speed_tier, base_rate) VALUES ('Express', 'express', 12) RETURNING id"
        );
        var overnightId = await conn.ExecuteScalarAsync<Guid>(
            "INSERT INTO carriers (name, speed_tier, base_rate) VALUES ('Overnight', 'overnight', 25) RETURNING id"
        );

        await conn.ExecuteAsync(
            @"INSERT INTO shipping_rates
                (carrier_id, region_from, region_to, price, estimated_days_min, estimated_days_max)
              VALUES
                (@std, 'east', 'west', 7,  5, 7),
                (@exp, 'east', 'west', 15, 2, 3),
                (@ovn, 'east', 'west', 30, 1, 1),
                (@std, 'east', 'east', 4,  2, 3),
                (@exp, 'east', 'east', 9,  1, 2)",
            new { std = standardId, exp = expressId, ovn = overnightId }
        );

        const string addr = "{\"street\":\"100 Test\",\"city\":\"SF\",\"state\":\"CA\",\"zip\":\"94105\",\"country\":\"US\"}";
        _orderPlacedId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO orders (user_id, status, total, shipping_address)
              VALUES (@uid, 'placed', 200, @addr::jsonb) RETURNING id",
            new { uid = userId, addr }
        );
        _orderShippedId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO orders (user_id, status, total, shipping_address, shipping_carrier, tracking_number)
              VALUES (@uid, 'shipped', 200, @addr::jsonb, 'Express', 'TRK123') RETURNING id",
            new { uid = userId, addr }
        );

        await conn.ExecuteAsync(
            @"INSERT INTO order_status_history (order_id, status, notes, location, timestamp)
              VALUES (@oid, 'placed',  'Order placed',  'East DC', NOW() - INTERVAL '3 days'),
                     (@oid, 'shipped', 'Out of dock',   'East DC', NOW() - INTERVAL '1 day')",
            new { oid = _orderShippedId }
        );
    }
}
