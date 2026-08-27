using Dapper;
using ECommerceAgents.Shared.Data;
using Microsoft.Extensions.AI;
using System.ComponentModel;

namespace ECommerceAgents.Shared.Tools;

/// <summary>
/// Cross-warehouse stock lookups — the .NET twin of Python's
/// <c>shared/tools/inventory_tools.py</c> (#18).
/// </summary>
/// <remarks>
/// Attached to the same two agents Python attaches it to: product-discovery
/// (so "is this in stock?" can be answered without a second hop) and
/// inventory-fulfillment.
///
/// Worth noting for anyone comparing the two trees: <c>check_stock</c> already
/// existed in .NET as an <b>MCP server</b> tool
/// (<c>ECommerceAgents.Mcp/McpTools.cs</c>), but no .NET specialist wires an
/// MCP client, so it was unreachable from any agent. This is a direct-SQL
/// implementation for the same reason Python keeps both: the MCP path is an
/// alternate delivery mechanism, not a replacement.
/// </remarks>
public sealed class StockLookupTools(DatabasePool pool)
{
    private readonly DatabasePool _pool = pool;

    public IEnumerable<AITool> All() => new AITool[]
    {
        AgentTool.Create(CheckStock, nameof(CheckStock)),
        AgentTool.Create(GetWarehouseAvailability, nameof(GetWarehouseAvailability)),
    };

    private static string FormatDate(object? value) => value switch
    {
        DateOnly d => d.ToString("yyyy-MM-dd"),
        DateTime dt => dt.ToString("yyyy-MM-dd"),
        _ => value?.ToString() ?? "",
    };

    [Description("Check stock levels across all warehouses for a specific product.")]
    public async Task<object> CheckStock(
        [Description("UUID of the product to check")] string productId
    )
    {
        if (!Guid.TryParse(productId, out var id))
        {
            return new { product_id = productId, in_stock = false, warehouses = Array.Empty<object>(), total_quantity = 0 };
        }

        await using var conn = await _pool.OpenAsync();
        var rows = (await conn.QueryAsync(
            @"SELECT w.name AS warehouse, w.region, wi.quantity, wi.reorder_threshold
              FROM warehouse_inventory wi
              JOIN warehouses w ON wi.warehouse_id = w.id
              WHERE wi.product_id = @id
              ORDER BY w.region",
            new { id }
        )).ToList();

        if (rows.Count == 0)
        {
            return new { product_id = productId, in_stock = false, warehouses = Array.Empty<object>(), total_quantity = 0 };
        }

        var warehouses = rows.Select(r => new
        {
            warehouse = (string)r.warehouse,
            region = (string)r.region,
            quantity = (int)r.quantity,
            low_stock = (int)r.quantity <= (int)r.reorder_threshold,
        }).ToList();

        var total = warehouses.Sum(w => w.quantity);

        return new
        {
            product_id = productId,
            in_stock = total > 0,
            total_quantity = total,
            warehouses,
        };
    }

    [Description("Get detailed warehouse availability for a product including restock schedules.")]
    public async Task<object> GetWarehouseAvailability(
        [Description("UUID of the product")] string productId
    )
    {
        if (!Guid.TryParse(productId, out var id))
        {
            return new { product_id = productId, warehouses = Array.Empty<object>(), upcoming_restocks = Array.Empty<object>() };
        }

        await using var conn = await _pool.OpenAsync();

        var inventory = (await conn.QueryAsync(
            @"SELECT w.name, w.region, w.location, wi.quantity, wi.reorder_threshold
              FROM warehouse_inventory wi
              JOIN warehouses w ON wi.warehouse_id = w.id
              WHERE wi.product_id = @id",
            new { id }
        )).Select(r => new
        {
            name = (string)r.name,
            region = (string)r.region,
            location = (string)r.location,
            quantity = (int)r.quantity,
            low_stock = (int)r.quantity <= (int)r.reorder_threshold,
        }).ToList();

        var restocks = (await conn.QueryAsync(
            @"SELECT w.name AS warehouse, rs.expected_quantity, rs.expected_date
              FROM restock_schedule rs
              JOIN warehouses w ON rs.warehouse_id = w.id
              WHERE rs.product_id = @id AND rs.expected_date >= CURRENT_DATE
              ORDER BY rs.expected_date",
            new { id }
        )).Select(r => new
        {
            warehouse = (string)r.warehouse,
            expected_quantity = (int)r.expected_quantity,
            // restock_schedule.expected_date is a DATE, which Npgsql maps to
            // DateOnly rather than DateTime — casting straight to DateTime
            // throws at runtime, and only a real-database test catches it.
            expected_date = FormatDate(r.expected_date),
        }).ToList();

        return new { product_id = productId, warehouses = inventory, upcoming_restocks = restocks };
    }
}
