using Dapper;
using ECommerceAgents.Shared.Data;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;
using Microsoft.Extensions.DependencyInjection;

namespace ECommerceAgents.Mcp;

/// <summary>
/// MCP endpoints ported 1:1 from
/// <c>agents/python/mcp/inventory_server.py</c>. Three tools are
/// exposed via the standard MCP discovery manifest and dispatched
/// through <c>POST /mcp/tools/{tool_name}</c>.
/// </summary>
public static class McpEndpoints
{
    public sealed record CheckStockParams(string? ProductId);
    public sealed record EstimateShippingParams(string? ProductId, string? DestinationRegion);

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

    public static IEndpointRouteBuilder MapMcpEndpoints(this IEndpointRouteBuilder routes)
    {
        routes.MapGet("/", () =>
            Results.Ok(new { service = "mcp-inventory", healthy = true })
        );
        routes.MapGet("/health", () => Results.Ok(new { healthy = true }));
        routes.MapGet("/.well-known/mcp.json", Manifest);
        routes.MapPost("/mcp/tools/{toolName}", ExecuteTool);
        return routes;
    }

    // ─────────────────────── manifest ───────────────────────

    private static IResult Manifest() =>
        Results.Ok(new
        {
            name = "inventory-mcp",
            version = "1.0",
            description = "Inventory and fulfillment data via MCP",
            tools = new object[]
            {
                new
                {
                    name = "check_stock",
                    description = "Check product stock levels across all warehouses",
                    parameters = new
                    {
                        type = "object",
                        properties = new
                        {
                            product_id = new { type = "string", description = "Product UUID" },
                        },
                        required = new[] { "product_id" },
                    },
                },
                new
                {
                    name = "get_warehouses",
                    description = "List all warehouses with their regions and capacity",
                    parameters = new { type = "object", properties = new { } },
                },
                new
                {
                    name = "estimate_shipping",
                    description = "Estimate shipping cost and delivery time",
                    parameters = new
                    {
                        type = "object",
                        properties = new
                        {
                            product_id = new { type = "string", description = "Product UUID" },
                            destination_region = new
                            {
                                type = "string",
                                @enum = new[] { "east", "central", "west" },
                                description = "Destination region",
                            },
                        },
                        required = new[] { "product_id", "destination_region" },
                    },
                },
            },
        });

    // ─────────────────────── dispatch ───────────────────────

    private static async Task<IResult> ExecuteTool(
        string toolName,
        HttpContext http,
        DatabasePool pool
    )
    {
        // Read body as raw JSON so the same handler shape accepts optional
        // params objects (get_warehouses has no body, check_stock has one).
        Dictionary<string, object?> body;
        try
        {
            if (http.Request.ContentLength is > 0)
            {
                var parsed = await http.Request
                    .ReadFromJsonAsync<Dictionary<string, object?>>();
                body = parsed ?? new();
            }
            else
            {
                body = new();
            }
        }
        catch
        {
            body = new();
        }

        return toolName switch
        {
            "check_stock" => Results.Ok(new { result = await CheckStock(pool, body) }),
            "get_warehouses" => Results.Ok(new { result = await GetWarehouses(pool) }),
            "estimate_shipping" => Results.Ok(new { result = await EstimateShipping(pool, body) }),
            _ => Results.NotFound(new { error = $"Unknown tool: {toolName}" }),
        };
    }

    // ─────────────────────── handlers (public for tests) ────

    public static async Task<CheckStockResult> CheckStock(DatabasePool pool, IDictionary<string, object?> body)
    {
        var productIdRaw = body.TryGetValue("product_id", out var v) ? v?.ToString() : null;
        if (!Guid.TryParse(productIdRaw, out var pid))
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

    public static async Task<EstimateShippingResult> EstimateShipping(DatabasePool pool, IDictionary<string, object?> body)
    {
        var productIdRaw = body.TryGetValue("product_id", out var v) ? v?.ToString() : null;
        var dest = body.TryGetValue("destination_region", out var d) ? d?.ToString() ?? "east" : "east";

        if (!Guid.TryParse(productIdRaw, out var pid))
        {
            return new EstimateShippingResult(false, null, null, "Invalid product_id");
        }

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
