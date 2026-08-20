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
/// <see cref="McpTools"/> so we don't need a live MCP client — the
/// behaviour we care about lives in the SQL + shape mapping, same as
/// calling any other typed method (the SDK's own job is dispatching a
/// JSON-RPC call to this exact method, covered by McpProtocolTests instead).
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
        var result = await McpTools.CheckStock(_pool, Guid.NewGuid().ToString());
        result.InStock.Should().BeFalse();
        result.TotalQuantity.Should().Be(0);
    }

    [Fact]
    public async Task CheckStock_NonUuidReturnsZero()
    {
        var result = await McpTools.CheckStock(_pool, "not-a-uuid");
        result.InStock.Should().BeFalse();
    }

    [Fact]
    public async Task CheckStock_ReturnsPerWarehouseBreakdown()
    {
        var result = await McpTools.CheckStock(_pool, _productId.ToString());
        result.InStock.Should().BeTrue();
        result.TotalQuantity.Should().Be(17); // seeded: 12 east + 5 west
        result.Warehouses.Should().HaveCount(2);
    }

    // ─────────────────────── get_warehouses ──────────────────

    [Fact]
    public async Task GetWarehouses_ListsAllSeeded()
    {
        var result = await McpTools.GetWarehouses(_pool);
        result.Should().HaveCount(2);
        result.Should().Contain(w => w.Region == "east");
        result.Should().Contain(w => w.Region == "west");
    }

    // ─────────────────────── estimate_shipping ───────────────

    [Fact]
    public async Task EstimateShipping_UnknownProductReportsUnavailable()
    {
        var result = await McpTools.EstimateShipping(_pool, Guid.NewGuid().ToString(), "east");
        result.Available.Should().BeFalse();
    }

    [Fact]
    public async Task EstimateShipping_InvalidProductIdReportsUnavailable()
    {
        var result = await McpTools.EstimateShipping(_pool, "not-a-uuid", "east");
        result.Available.Should().BeFalse();
        result.Message.Should().Contain("Invalid");
    }

    [Fact]
    public async Task EstimateShipping_PrefersSameRegionWarehouse()
    {
        var result = await McpTools.EstimateShipping(_pool, _productId.ToString(), "east");
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

        var result = await McpTools.EstimateShipping(_pool, _productId.ToString(), "east");
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
