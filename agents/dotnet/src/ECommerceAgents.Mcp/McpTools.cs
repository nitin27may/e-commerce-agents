using System.ComponentModel;
using Dapper;
using ECommerceAgents.Shared.Data;
using ModelContextProtocol.Server;

namespace ECommerceAgents.Mcp;

/// <summary>
/// Real MCP tools, exposed over the protocol's own JSON-RPC dispatch and
/// <c>tools/list</c> discovery via <see cref="ModelContextProtocol.AspNetCore"/>'s
/// <c>MapMcp()</c> — parity with the Python reference at
/// <c>agents/python/mcp_servers/inventory_server.py</c> (a real FastMCP
/// streamable-HTTP server). Previously this project exposed the same three
/// operations as a hand-rolled REST surface (<c>POST /mcp/tools/{name}</c> +
/// a custom <c>/.well-known/mcp.json</c> manifest) — not the MCP protocol,
/// just REST shaped to look like it.
///
/// <c>DatabasePool</c> is resolved from the DI container per call (typed
/// parameters not in the container become the tool's JSON-schema arguments —
/// same binding rule ASP.NET Core Minimal API endpoint handlers use), so it
/// never appears in the generated tool schema a client sees.
/// </summary>
[McpServerToolType]
public static class McpTools
{
    public sealed record WarehouseStock(string Warehouse, string Region, int Quantity, bool LowStock);
    public sealed record CheckStockResult(bool InStock, int TotalQuantity, List<WarehouseStock> Warehouses);
    public sealed record WarehouseInfo(string Id, string Name, string Region, string Location);
    public sealed record ShippingOptionInfo(string Carrier, decimal Price, string Days);
    public sealed record EstimateShippingResult(
        bool Available,
        string? ShipsFrom,
        List<ShippingOptionInfo>? Options,
        string? Message
    );

    [McpServerTool(Name = "check_stock")]
    [Description("Check product stock levels across all warehouses.")]
    public static async Task<CheckStockResult> CheckStock(
        DatabasePool pool,
        [Description("Product UUID")] string productId
    )
    {
        if (!Guid.TryParse(productId, out var pid))
        {
            return new CheckStockResult(false, 0, new List<WarehouseStock>());
        }

        await using var conn = await pool.OpenAsync();
        var rows = (await conn.QueryAsync(
            @"SELECT w.name AS warehouse, w.region, wi.quantity,
                     wi.quantity <= wi.reorder_threshold AS low_stock
              FROM warehouse_inventory wi
              JOIN warehouses w ON wi.warehouse_id = w.id
              WHERE wi.product_id = @pid",
            new { pid }
        )).ToList();

        if (rows.Count == 0)
        {
            return new CheckStockResult(false, 0, new List<WarehouseStock>());
        }

        var warehouses = rows.Select(r => new WarehouseStock(
            Warehouse: (string)r.warehouse,
            Region: (string)r.region,
            Quantity: (int)r.quantity,
            LowStock: (bool)r.low_stock
        )).ToList();
        var total = warehouses.Sum(w => w.Quantity);
        return new CheckStockResult(total > 0, total, warehouses);
    }

    [McpServerTool(Name = "get_warehouses")]
    [Description("List all warehouses with their regions and capacity.")]
    public static async Task<List<WarehouseInfo>> GetWarehouses(DatabasePool pool)
    {
        await using var conn = await pool.OpenAsync();
        return (await conn.QueryAsync(
            "SELECT id, name, region, location FROM warehouses ORDER BY name"
        )).Select(r => new WarehouseInfo(
            Id: ((Guid)r.id).ToString(),
            Name: (string)r.name,
            Region: (string)r.region,
            Location: (string)r.location
        )).ToList();
    }

    [McpServerTool(Name = "estimate_shipping")]
    [Description("Estimate shipping cost and delivery time for a product to a destination region.")]
    public static async Task<EstimateShippingResult> EstimateShipping(
        DatabasePool pool,
        [Description("Product UUID")] string productId,
        [Description("Destination region: east, central, or west")] string destinationRegion
    )
    {
        if (!Guid.TryParse(productId, out var pid))
        {
            return new EstimateShippingResult(false, null, null, "Invalid product_id");
        }

        var dest = string.IsNullOrWhiteSpace(destinationRegion) ? "east" : destinationRegion;

        await using var conn = await pool.OpenAsync();
        var row = await conn.QueryFirstOrDefaultAsync(
            @"SELECT w.region, wi.quantity
              FROM warehouse_inventory wi
              JOIN warehouses w ON wi.warehouse_id = w.id
              WHERE wi.product_id = @pid AND wi.quantity > 0
              ORDER BY CASE w.region
                  WHEN @dest THEN 0
                  WHEN 'central' THEN 1
                  ELSE 2
              END
              LIMIT 1",
            new { pid, dest }
        );
        if (row is null)
        {
            return new EstimateShippingResult(
                false, null, null, "Product out of stock in all warehouses"
            );
        }

        string regionFrom = (string)row.region;
        var rates = (await conn.QueryAsync(
            @"SELECT c.name AS carrier, sr.price, sr.estimated_days_min, sr.estimated_days_max
              FROM shipping_rates sr
              JOIN carriers c ON sr.carrier_id = c.id
              WHERE sr.region_from = @from AND sr.region_to = @to
              ORDER BY sr.price",
            new { from = regionFrom, to = dest }
        )).Select(r => new ShippingOptionInfo(
            Carrier: (string)r.carrier,
            Price: (decimal)r.price,
            Days: $"{(int)r.estimated_days_min}-{(int)r.estimated_days_max}"
        )).ToList();

        return new EstimateShippingResult(true, regionFrom, rates, null);
    }
}
