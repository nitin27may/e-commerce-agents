using Dapper;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using Microsoft.Extensions.AI;
using System.ComponentModel;

namespace ECommerceAgents.InventoryFulfillment.Tools;

/// <summary>
/// MAF tools for the InventoryFulfillment specialist. Mirrors
/// <c>agents/python/inventory_fulfillment/tools.py</c> for the four
/// read-only inventory + shipping tools. The two state-mutating tools
/// (calculate_fulfillment_plan, place_backorder) ship in a follow-up.
/// </summary>
public sealed class InventoryTools(DatabasePool pool)
{
    private readonly DatabasePool _pool = pool;

    public IEnumerable<AITool> All() => new AITool[]
    {
        AIFunctionFactory.Create(GetRestockSchedule, nameof(GetRestockSchedule)),
        AIFunctionFactory.Create(EstimateShipping, nameof(EstimateShipping)),
        AIFunctionFactory.Create(CompareCarriers, nameof(CompareCarriers)),
        AIFunctionFactory.Create(GetTrackingStatus, nameof(GetTrackingStatus)),
    };

    // ─────────────────────── get_restock_schedule ────────────

    [Description("Get upcoming restock schedule for a product across all warehouses.")]
    public async Task<RestockScheduleResult> GetRestockSchedule(
        [Description("UUID of the product")] string productId
    )
    {
        if (!Guid.TryParse(productId, out var pid))
        {
            return RestockScheduleResult.Failure($"Product not found: {productId}");
        }

        await using var conn = await _pool.OpenAsync();
        var product = await conn.QueryFirstOrDefaultAsync(
            "SELECT id, name FROM products WHERE id = @pid",
            new { pid }
        );
        if (product is null)
        {
            return RestockScheduleResult.Failure($"Product not found: {productId}");
        }

        var rowsRaw = (await conn.QueryAsync(
            @"SELECT rs.expected_quantity, rs.expected_date,
                     w.name AS warehouse, w.region
              FROM restock_schedule rs
              JOIN warehouses w ON rs.warehouse_id = w.id
              WHERE rs.product_id = @pid AND rs.expected_date >= CURRENT_DATE
              ORDER BY rs.expected_date",
            new { pid }
        )).ToList();

        var rows = rowsRaw.Select(r => new RestockEntry(
            Warehouse: (string)r.warehouse,
            Region: (string)r.region,
            ExpectedQuantity: (int)r.expected_quantity,
            ExpectedDate: ((DateTime)r.expected_date).ToString("yyyy-MM-dd")
        )).ToList();

        return new RestockScheduleResult(
            Error: null,
            ProductId: productId,
            ProductName: (string)product.name,
            UpcomingRestocks: rows,
            NextRestock: rows.Count == 0 ? null : rows[0].ExpectedDate
        );
    }

    // ─────────────────────── estimate_shipping ───────────────

    [Description("Estimate shipping cost and delivery time for a product to a destination region. Finds the closest warehouse with stock and returns carrier options.")]
    public async Task<ShippingEstimateResult> EstimateShipping(
        [Description("UUID of the product")] string productId,
        [Description("Destination region: 'east', 'central', or 'west'")] string destinationRegion
    )
    {
        if (!Guid.TryParse(productId, out var pid))
        {
            return ShippingEstimateResult.Unavailable(productId, destinationRegion, "Product not found");
        }

        await using var conn = await _pool.OpenAsync();
        var inventoryRaw = (await conn.QueryAsync(
            @"SELECT w.id AS warehouse_id, w.name AS warehouse, w.region, wi.quantity
              FROM warehouse_inventory wi
              JOIN warehouses w ON wi.warehouse_id = w.id
              WHERE wi.product_id = @pid AND wi.quantity > 0
              ORDER BY
                  CASE WHEN w.region = @region THEN 0 ELSE 1 END,
                  wi.quantity DESC",
            new { pid, region = destinationRegion }
        )).ToList();

        if (inventoryRaw.Count == 0)
        {
            return ShippingEstimateResult.Unavailable(
                productId,
                destinationRegion,
                "Product is out of stock at all warehouses. Check restock schedule."
            );
        }

        var best = inventoryRaw[0];
        string regionFrom = (string)best.region;

        var rateRowsRaw = (await conn.QueryAsync(
            @"SELECT c.name AS carrier, c.speed_tier,
                     sr.price, sr.estimated_days_min, sr.estimated_days_max
              FROM shipping_rates sr
              JOIN carriers c ON sr.carrier_id = c.id
              WHERE sr.region_from = @from AND sr.region_to = @to
              ORDER BY sr.price",
            new { from = regionFrom, to = destinationRegion }
        )).ToList();

        var options = rateRowsRaw.Select(r =>
        {
            int dMin = (int)r.estimated_days_min;
            int dMax = (int)r.estimated_days_max;
            return new ShippingOption(
                Carrier: (string)r.carrier,
                SpeedTier: (string)r.speed_tier,
                Price: (decimal)r.price,
                EstimatedDaysMin: dMin,
                EstimatedDaysMax: dMax,
                DeliveryWindow: $"{dMin}-{dMax} business days"
            );
        }).ToList();

        return new ShippingEstimateResult(
            ProductId: productId,
            DestinationRegion: destinationRegion,
            Available: true,
            Message: null,
            ShipsFrom: new ShipsFromInfo(
                Warehouse: (string)best.warehouse,
                Region: regionFrom,
                QuantityAvailable: (int)best.quantity
            ),
            ShippingOptions: options
        );
    }

    // ─────────────────────── compare_carriers ────────────────

    [Description("Compare all carrier options (Standard, Express, Overnight) between two regions with pricing and delivery estimates.")]
    public async Task<CarrierComparisonResult> CompareCarriers(
        [Description("Origin region: 'east', 'central', or 'west'")] string regionFrom,
        [Description("Destination region: 'east', 'central', or 'west'")] string regionTo
    )
    {
        await using var conn = await _pool.OpenAsync();
        var rowsRaw = (await conn.QueryAsync(
            @"SELECT c.name AS carrier, c.speed_tier, c.base_rate,
                     sr.price, sr.estimated_days_min, sr.estimated_days_max
              FROM shipping_rates sr
              JOIN carriers c ON sr.carrier_id = c.id
              WHERE sr.region_from = @from AND sr.region_to = @to
              ORDER BY sr.price",
            new { from = regionFrom, to = regionTo }
        )).ToList();

        if (rowsRaw.Count == 0)
        {
            return new CarrierComparisonResult(
                RegionFrom: regionFrom,
                RegionTo: regionTo,
                Carriers: [],
                BestValue: null,
                Fastest: null,
                Message: "No shipping rates found for this route."
            );
        }

        var carriers = rowsRaw.Select(r =>
        {
            int dMin = (int)r.estimated_days_min;
            int dMax = (int)r.estimated_days_max;
            return new CarrierOption(
                Carrier: (string)r.carrier,
                SpeedTier: (string)r.speed_tier,
                Price: (decimal)r.price,
                BaseRate: (decimal)r.base_rate,
                EstimatedDaysMin: dMin,
                EstimatedDaysMax: dMax,
                DeliveryWindow: $"{dMin}-{dMax} business days"
            );
        }).ToList();

        var cheapest = carriers.OrderBy(c => c.Price).First();
        var fastest = carriers.OrderBy(c => c.EstimatedDaysMin).First();

        return new CarrierComparisonResult(
            RegionFrom: regionFrom,
            RegionTo: regionTo,
            Carriers: carriers,
            BestValue: cheapest.Carrier,
            Fastest: fastest.Carrier,
            Message: null
        );
    }

    // ─────────────────────── get_tracking_status ─────────────

    [Description("Get the latest tracking and shipment status for an order.")]
    public async Task<TrackingStatusResult> GetTrackingStatus(
        [Description("UUID of the order")] string orderId
    )
    {
        var email = RequestContext.CurrentUserEmail;
        if (string.IsNullOrEmpty(email))
        {
            return TrackingStatusResult.Failure("No user context available");
        }
        if (!Guid.TryParse(orderId, out var oid))
        {
            return TrackingStatusResult.Failure($"Order not found or not accessible: {orderId}");
        }

        await using var conn = await _pool.OpenAsync();
        var order = await conn.QueryFirstOrDefaultAsync(
            @"SELECT o.id, o.status, o.tracking_number, o.shipping_carrier,
                     o.shipping_address, o.created_at
              FROM orders o
              JOIN users u ON o.user_id = u.id
              WHERE o.id = @id AND u.email = @email",
            new { id = oid, email }
        );
        if (order is null)
        {
            return TrackingStatusResult.Failure($"Order not found or not accessible: {orderId}");
        }

        var status = (string)order.status;
        if (status is not "shipped" and not "out_for_delivery" and not "delivered")
        {
            return new TrackingStatusResult(
                Error: null,
                OrderId: ((Guid)order.id).ToString(),
                Status: status,
                TrackingNumber: null,
                ShippingCarrier: null,
                History: [],
                LatestUpdate: null,
                Message: $"Order is currently '{status}' — tracking is available once shipped."
            );
        }

        var historyRaw = (await conn.QueryAsync(
            @"SELECT status, notes, location, timestamp
              FROM order_status_history
              WHERE order_id = @id
              ORDER BY timestamp DESC",
            new { id = oid }
        )).ToList();

        var history = historyRaw.Select(h => new TrackingEvent(
            Status: (string)h.status,
            Notes: (string?)h.notes,
            Location: (string?)h.location,
            Timestamp: ((DateTime)h.timestamp).ToString("o")
        )).ToList();

        TrackingEvent? latest = history.Count == 0 ? null : history[0];

        return new TrackingStatusResult(
            Error: null,
            OrderId: ((Guid)order.id).ToString(),
            Status: status,
            TrackingNumber: (string?)order.tracking_number,
            ShippingCarrier: (string?)order.shipping_carrier,
            History: history,
            LatestUpdate: latest,
            Message: null
        );
    }
}

// ─────────────────────── DTOs ───────────────────────

public sealed record RestockEntry(string Warehouse, string Region, int ExpectedQuantity, string ExpectedDate);

public sealed record RestockScheduleResult(
    string? Error,
    string ProductId,
    string? ProductName,
    List<RestockEntry>? UpcomingRestocks,
    string? NextRestock
)
{
    public static RestockScheduleResult Failure(string error) => new(error, "", null, null, null);
}

public sealed record ShipsFromInfo(string Warehouse, string Region, int QuantityAvailable);

public sealed record ShippingOption(
    string Carrier,
    string SpeedTier,
    decimal Price,
    int EstimatedDaysMin,
    int EstimatedDaysMax,
    string DeliveryWindow
);

public sealed record ShippingEstimateResult(
    string ProductId,
    string DestinationRegion,
    bool Available,
    string? Message,
    ShipsFromInfo? ShipsFrom,
    List<ShippingOption>? ShippingOptions
)
{
    public static ShippingEstimateResult Unavailable(string productId, string region, string msg) =>
        new(productId, region, false, msg, null, null);
}

public sealed record CarrierOption(
    string Carrier,
    string SpeedTier,
    decimal Price,
    decimal BaseRate,
    int EstimatedDaysMin,
    int EstimatedDaysMax,
    string DeliveryWindow
);

public sealed record CarrierComparisonResult(
    string RegionFrom,
    string RegionTo,
    List<CarrierOption> Carriers,
    string? BestValue,
    string? Fastest,
    string? Message
);

public sealed record TrackingEvent(string Status, string? Notes, string? Location, string Timestamp);

public sealed record TrackingStatusResult(
    string? Error,
    string OrderId,
    string? Status,
    string? TrackingNumber,
    string? ShippingCarrier,
    List<TrackingEvent>? History,
    TrackingEvent? LatestUpdate,
    string? Message
)
{
    public static TrackingStatusResult Failure(string error) =>
        new(error, "", null, null, null, null, null, null);
}
