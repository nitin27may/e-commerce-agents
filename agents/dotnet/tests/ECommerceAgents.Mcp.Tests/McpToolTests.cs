using Dapper;
using ECommerceAgents.Mcp;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using Xunit;

namespace ECommerceAgents.Mcp.Tests;

[CollectionDefinition(nameof(LocalPostgresCollection))]
public sealed class LocalPostgresCollection : ICollectionFixture<PostgresFixture> { }

/// <summary>
/// Tests the three MCP tool handlers against a real Postgres
/// testcontainer. The handlers are exposed as public static methods on
/// <see cref="McpEndpoints"/> so we don't need an HTTP test server —
/// the behaviour we care about lives in the SQL + shape mapping.
/// </summary>
[Collection(nameof(LocalPostgresCollection))]
public sealed class McpToolTests : IAsyncLifetime
{
    private readonly PostgresFixture _pg;
    private DatabasePool _pool = null!;
    private Guid _productId;
    private Guid _warehouseEast;
    private Guid _warehouseWest;

    public McpToolTests(PostgresFixture pg) => _pg = pg;

    public async Task InitializeAsync()
    {
        var settings = new AgentSettings { DatabaseUrl = _pg.ConnectionString };
        _pool = new DatabasePool(settings);
        await SeedAsync();
    }

    public async Task DisposeAsync()
    {
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            @"TRUNCATE shipping_rates, carriers, warehouse_inventory,
                       warehouses, products RESTART IDENTITY CASCADE"
        );
        await _pool.DisposeAsync();
    }

    // ─────────────────────── check_stock ─────────────────────

    [Fact]
    public async Task CheckStock_UnknownProductReturnsZero()
    {
        var body = new Dictionary<string, object?> { ["product_id"] = Guid.NewGuid().ToString() };
        var result = await McpEndpoints.CheckStock(_pool, body);
        result.InStock.Should().BeFalse();
        result.TotalQuantity.Should().Be(0);
    }

    [Fact]
    public async Task CheckStock_NonUuidReturnsZero()
    {
        var body = new Dictionary<string, object?> { ["product_id"] = "not-a-uuid" };
        var result = await McpEndpoints.CheckStock(_pool, body);
        result.InStock.Should().BeFalse();
    }

    [Fact]
    public async Task CheckStock_ReturnsPerWarehouseBreakdown()
    {
        var body = new Dictionary<string, object?> { ["product_id"] = _productId.ToString() };
        var result = await McpEndpoints.CheckStock(_pool, body);
        result.InStock.Should().BeTrue();
        result.TotalQuantity.Should().Be(17); // seeded: 12 east + 5 west
        result.Warehouses.Should().HaveCount(2);
    }

    // ─────────────────────── get_warehouses ──────────────────

    [Fact]
    public async Task GetWarehouses_ListsAllSeeded()
    {
        var result = await McpEndpoints.GetWarehouses(_pool);
        result.Should().HaveCount(2);
        result.Should().Contain(w => w.Region == "east");
        result.Should().Contain(w => w.Region == "west");
    }

    // ─────────────────────── estimate_shipping ───────────────

    [Fact]
    public async Task EstimateShipping_UnknownProductReportsUnavailable()
    {
        var body = new Dictionary<string, object?>
        {
            ["product_id"] = Guid.NewGuid().ToString(),
            ["destination_region"] = "east",
        };
        var result = await McpEndpoints.EstimateShipping(_pool, body);
        result.Available.Should().BeFalse();
    }

    [Fact]
    public async Task EstimateShipping_InvalidProductIdReportsUnavailable()
    {
        var body = new Dictionary<string, object?>
        {
            ["product_id"] = "not-a-uuid",
            ["destination_region"] = "east",
        };
        var result = await McpEndpoints.EstimateShipping(_pool, body);
        result.Available.Should().BeFalse();
        result.Message.Should().Contain("Invalid");
    }

    [Fact]
    public async Task EstimateShipping_PrefersSameRegionWarehouse()
    {
        var body = new Dictionary<string, object?>
        {
            ["product_id"] = _productId.ToString(),
            ["destination_region"] = "east",
        };
        var result = await McpEndpoints.EstimateShipping(_pool, body);
        result.Available.Should().BeTrue();
        result.ShipsFrom.Should().Be("east");
        result.Options.Should().NotBeNullOrEmpty();
    }

    [Fact]
    public async Task EstimateShipping_FallsBackWhenNoSameRegionStock()
    {
        await using (var conn = await _pool.OpenAsync())
        {
            await conn.ExecuteAsync(
                "UPDATE warehouse_inventory SET quantity = 0 WHERE warehouse_id = @id",
                new { id = _warehouseEast }
            );
        }

        var body = new Dictionary<string, object?>
        {
            ["product_id"] = _productId.ToString(),
            ["destination_region"] = "east",
        };
        var result = await McpEndpoints.EstimateShipping(_pool, body);
        result.Available.Should().BeTrue();
        result.ShipsFrom.Should().Be("west");
    }

    // ─────────────────────── seed ────────────────────────────

    private async Task SeedAsync()
    {
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            @"TRUNCATE shipping_rates, carriers, warehouse_inventory,
                       warehouses, products RESTART IDENTITY CASCADE"
        );

        _productId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO products (name, description, category, brand, price)
              VALUES ('Headphones', 'Sample', 'Electronics', 'X', 200)
              RETURNING id"
        );
        _warehouseEast = await conn.ExecuteScalarAsync<Guid>(
            "INSERT INTO warehouses (name, location, region) VALUES ('East', 'Richmond, VA', 'east') RETURNING id"
        );
        _warehouseWest = await conn.ExecuteScalarAsync<Guid>(
            "INSERT INTO warehouses (name, location, region) VALUES ('West', 'San Jose, CA', 'west') RETURNING id"
        );
        await conn.ExecuteAsync(
            @"INSERT INTO warehouse_inventory (warehouse_id, product_id, quantity, reorder_threshold)
              VALUES (@east, @pid, 12, 10), (@west, @pid, 5, 10)",
            new { east = _warehouseEast, west = _warehouseWest, pid = _productId }
        );

        var std = await conn.ExecuteScalarAsync<Guid>(
            "INSERT INTO carriers (name, speed_tier, base_rate) VALUES ('Standard', 'standard', 5) RETURNING id"
        );
        await conn.ExecuteAsync(
            @"INSERT INTO shipping_rates
                (carrier_id, region_from, region_to, price, estimated_days_min, estimated_days_max)
              VALUES (@std, 'east', 'east', 4, 2, 3),
                     (@std, 'west', 'east', 9, 4, 6)",
            new { std }
        );
    }
}
