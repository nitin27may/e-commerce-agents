using Dapper;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using Microsoft.Extensions.AI;
using Npgsql;
using System.ComponentModel;
using System.Text.Json;

namespace ECommerceAgents.OrderManagement.Tools;

/// <summary>
/// MAF tools for the OrderManagement specialist. Mirrors
/// <c>agents/python/order_management/tools.py</c> 1:1 — same SQL,
/// same identity scoping (every query joins to <c>users.email</c>),
/// same approval-required gating on the destructive tools.
/// </summary>
/// <remarks>
/// Read-only tools: <see cref="GetUserOrders"/>, <see cref="GetOrderDetails"/>,
/// <see cref="GetOrderTracking"/>.<br/>
/// State-mutating tools (gated for human approval upstream and wrapped
/// in row-locked transactions to avoid double-cancel / double-modify):
/// <see cref="CancelOrder"/>, <see cref="ModifyOrder"/>.
/// </remarks>
public sealed class OrderTools(DatabasePool pool)
{
    private readonly DatabasePool _pool = pool;

    /// <summary>Hard ceiling for the LLM-supplied <c>limit</c> parameter on list queries.</summary>
    private const int MaxLimit = 100;

    /// <summary>Allowed terminal-state values for <c>cancel_order</c> source rows.</summary>
    private static readonly HashSet<string> CancellableStatuses = new(StringComparer.Ordinal)
    {
        "placed",
        "confirmed",
    };

    private static readonly HashSet<string> ModifiableStatuses = CancellableStatuses;

    public IEnumerable<AITool> All() => new AITool[]
    {
        AIFunctionFactory.Create(GetUserOrders, nameof(GetUserOrders)),
        AIFunctionFactory.Create(GetOrderDetails, nameof(GetOrderDetails)),
        AIFunctionFactory.Create(GetOrderTracking, nameof(GetOrderTracking)),
        AIFunctionFactory.Create(CancelOrder, nameof(CancelOrder)),
        AIFunctionFactory.Create(ModifyOrder, nameof(ModifyOrder)),
    };

    // ─────────────────────── Read-only ───────────────────────

    [Description("List orders for the current user, optionally filtered by status.")]
    public async Task<List<OrderSummary>> GetUserOrders(
        [Description("Filter by order status (placed / confirmed / shipped / out_for_delivery / delivered / cancelled / returned)")]
            string? status = null,
        [Description("Max number of orders to return (capped at 100)")] int limit = 10
    )
    {
        var email = RequestContext.CurrentUserEmail;
        if (string.IsNullOrEmpty(email))
        {
            return [];
        }

        var clamped = Math.Clamp(limit, 1, MaxLimit);
        var sql = @"
            SELECT o.id, o.status, o.total, o.discount_amount, o.coupon_code,
                   o.shipping_carrier, o.tracking_number, o.created_at,
                   COUNT(oi.id) AS item_count
            FROM orders o
            JOIN users u ON o.user_id = u.id
            LEFT JOIN order_items oi ON oi.order_id = o.id
            WHERE u.email = @email
              AND (@status::text IS NULL OR o.status = @status)
            GROUP BY o.id, o.status, o.total, o.discount_amount, o.coupon_code,
                     o.shipping_carrier, o.tracking_number, o.created_at
            ORDER BY o.created_at DESC
            LIMIT @limit";

        await using var conn = await _pool.OpenAsync();
        var rows = await conn.QueryAsync(sql, new { email, status, limit = clamped });
        return rows.Select(r => new OrderSummary(
            OrderId: ((Guid)r.id).ToString(),
            Status: (string)r.status,
            Total: (decimal)r.total,
            DiscountAmount: r.discount_amount is null ? 0m : (decimal)r.discount_amount,
            CouponCode: (string?)r.coupon_code,
            ShippingCarrier: (string?)r.shipping_carrier,
            TrackingNumber: (string?)r.tracking_number,
            ItemCount: Convert.ToInt32(r.item_count),
            CreatedAt: ((DateTime)r.created_at).ToString("o")
        )).ToList();
    }

    [Description("Get full order details including line items, status history, and tracking info.")]
    public async Task<OrderDetails?> GetOrderDetails(
        [Description("UUID of the order")] string orderId
    )
    {
        var email = RequestContext.CurrentUserEmail;
        if (string.IsNullOrEmpty(email) || !Guid.TryParse(orderId, out var orderGuid))
        {
            return null;
        }

        await using var conn = await _pool.OpenAsync();
        var order = await conn.QueryFirstOrDefaultAsync(
            @"SELECT o.id, o.status, o.total, o.shipping_address,
                     o.shipping_carrier, o.tracking_number,
                     o.coupon_code, o.discount_amount, o.created_at
              FROM orders o
              JOIN users u ON o.user_id = u.id
              WHERE o.id = @id AND u.email = @email",
            new { id = orderGuid, email }
        );
        if (order is null)
        {
            return null;
        }

        var items = (await conn.QueryAsync(
            @"SELECT oi.id, oi.quantity, oi.unit_price, oi.subtotal,
                     p.name, p.category, p.brand
              FROM order_items oi
              JOIN products p ON oi.product_id = p.id
              WHERE oi.order_id = @id",
            new { id = orderGuid }
        )).Select(i => new OrderItem(
            ItemId: ((Guid)i.id).ToString(),
            ProductName: (string)i.name,
            Category: (string)i.category,
            Brand: (string?)i.brand ?? "",
            Quantity: (int)i.quantity,
            UnitPrice: (decimal)i.unit_price,
            Subtotal: (decimal)i.subtotal
        )).ToList();

        var history = (await conn.QueryAsync(
            @"SELECT status, notes, location, timestamp
              FROM order_status_history
              WHERE order_id = @id
              ORDER BY timestamp DESC",
            new { id = orderGuid }
        )).Select(h => new OrderStatusEntry(
            Status: (string)h.status,
            Notes: (string?)h.notes,
            Location: (string?)h.location,
            Timestamp: ((DateTime)h.timestamp).ToString("o")
        )).ToList();

        Dictionary<string, JsonElement>? shippingAddress = null;
        if (order.shipping_address is not null)
        {
            var raw = order.shipping_address is string s ? s : order.shipping_address.ToString();
            if (!string.IsNullOrWhiteSpace(raw))
            {
                shippingAddress = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(raw);
            }
        }

        return new OrderDetails(
            OrderId: ((Guid)order.id).ToString(),
            Status: (string)order.status,
            Total: (decimal)order.total,
            DiscountAmount: order.discount_amount is null ? 0m : (decimal)order.discount_amount,
            CouponCode: (string?)order.coupon_code,
            ShippingAddress: shippingAddress,
            ShippingCarrier: (string?)order.shipping_carrier,
            TrackingNumber: (string?)order.tracking_number,
            CreatedAt: ((DateTime)order.created_at).ToString("o"),
            Items: items,
            StatusHistory: history
        );
    }

    [Description("Get latest tracking status and location for an order.")]
    public async Task<OrderTracking?> GetOrderTracking(
        [Description("UUID of the order")] string orderId
    )
    {
        var email = RequestContext.CurrentUserEmail;
        if (string.IsNullOrEmpty(email) || !Guid.TryParse(orderId, out var orderGuid))
        {
            return null;
        }

        await using var conn = await _pool.OpenAsync();
        var order = await conn.QueryFirstOrDefaultAsync(
            @"SELECT o.id, o.status, o.shipping_carrier, o.tracking_number
              FROM orders o
              JOIN users u ON o.user_id = u.id
              WHERE o.id = @id AND u.email = @email",
            new { id = orderGuid, email }
        );
        if (order is null)
        {
            return null;
        }

        var status = (string)order.status;
        if (status is "placed" or "confirmed")
        {
            return new OrderTracking(
                OrderId: ((Guid)order.id).ToString(),
                Status: status,
                ShippingCarrier: null,
                TrackingNumber: null,
                LatestUpdate: null,
                Timeline: [],
                Message: "Order has not shipped yet. No tracking information available."
            );
        }

        var latest = await conn.QueryFirstOrDefaultAsync(
            @"SELECT status, notes, location, timestamp
              FROM order_status_history
              WHERE order_id = @id
              ORDER BY timestamp DESC
              LIMIT 1",
            new { id = orderGuid }
        );

        var timeline = (await conn.QueryAsync(
            @"SELECT status, notes, location, timestamp
              FROM order_status_history
              WHERE order_id = @id
              ORDER BY timestamp ASC",
            new { id = orderGuid }
        )).Select(t => new OrderStatusEntry(
            Status: (string)t.status,
            Notes: (string?)t.notes,
            Location: (string?)t.location,
            Timestamp: ((DateTime)t.timestamp).ToString("o")
        )).ToList();

        return new OrderTracking(
            OrderId: ((Guid)order.id).ToString(),
            Status: status,
            ShippingCarrier: (string?)order.shipping_carrier,
            TrackingNumber: (string?)order.tracking_number,
            LatestUpdate: latest is null
                ? null
                : new OrderStatusEntry(
                    Status: (string)latest.status,
                    Notes: (string?)latest.notes,
                    Location: (string?)latest.location,
                    Timestamp: ((DateTime)latest.timestamp).ToString("o")
                ),
            Timeline: timeline,
            Message: null
        );
    }

    // ─────────────────────── State-mutating ──────────────────

    [Description("Cancel an order. Only orders in 'placed' or 'confirmed' status can be cancelled.")]
    public async Task<CancelOrderResult> CancelOrder(
        [Description("UUID of the order to cancel")] string orderId,
        [Description("Reason for cancellation")] string reason
    )
    {
        var email = RequestContext.CurrentUserEmail;
        if (string.IsNullOrEmpty(email))
        {
            return CancelOrderResult.Failure("No user context available");
        }

        if (!Guid.TryParse(orderId, out var orderGuid))
        {
            return CancelOrderResult.Failure("order_id must be a UUID");
        }

        if (string.IsNullOrWhiteSpace(reason) || reason.Length > 500)
        {
            return CancelOrderResult.Failure("reason must be 1-500 characters");
        }

        await using var conn = await _pool.OpenAsync();
        await using var tx = await conn.BeginTransactionAsync();

        // FOR UPDATE OF o so a concurrent cancel blocks behind us.
        var order = await conn.QueryFirstOrDefaultAsync(
            @"SELECT o.id, o.status, o.total
              FROM orders o
              JOIN users u ON o.user_id = u.id
              WHERE o.id = @id AND u.email = @email
              FOR UPDATE OF o",
            new { id = orderGuid, email },
            tx
        );
        if (order is null)
        {
            return CancelOrderResult.Failure($"Order not found or access denied: {orderId}");
        }

        var currentStatus = (string)order.status;
        if (!CancellableStatuses.Contains(currentStatus))
        {
            return new CancelOrderResult(
                Error:
                    $"Cannot cancel order in '{currentStatus}' status. Only 'placed' or 'confirmed' orders can be cancelled.",
                OrderId: ((Guid)order.id).ToString(),
                CurrentStatus: currentStatus,
                PreviousStatus: null,
                NewStatus: null,
                Reason: null,
                RefundAmount: null,
                Message: null
            );
        }

        await conn.ExecuteAsync(
            "UPDATE orders SET status = 'cancelled' WHERE id = @id",
            new { id = orderGuid },
            tx
        );
        await conn.ExecuteAsync(
            @"INSERT INTO order_status_history (order_id, status, notes)
              VALUES (@id, 'cancelled', @notes)",
            new { id = orderGuid, notes = $"Cancelled by customer: {reason}" },
            tx
        );
        await tx.CommitAsync();

        var refundAmount = (decimal)order.total;
        return new CancelOrderResult(
            Error: null,
            OrderId: ((Guid)order.id).ToString(),
            CurrentStatus: null,
            PreviousStatus: currentStatus,
            NewStatus: "cancelled",
            Reason: reason,
            RefundAmount: refundAmount,
            Message:
                $"Order cancelled successfully. A refund of ${refundAmount:F2} will be processed within 5-7 business days."
        );
    }

    [Description("Modify the shipping address of an order. Only orders that haven't shipped yet can be modified.")]
    public async Task<ModifyOrderResult> ModifyOrder(
        [Description("UUID of the order to modify")] string orderId,
        [Description("New shipping address: street, city, state, zip, country")]
            ShippingAddressInput newAddress
    )
    {
        var email = RequestContext.CurrentUserEmail;
        if (string.IsNullOrEmpty(email))
        {
            return ModifyOrderResult.Failure("No user context available");
        }

        if (!Guid.TryParse(orderId, out var orderGuid))
        {
            return ModifyOrderResult.Failure("order_id must be a UUID");
        }

        var validation = newAddress.Validate();
        if (validation is not null)
        {
            return ModifyOrderResult.Failure(validation);
        }

        await using var conn = await _pool.OpenAsync();
        await using var tx = await conn.BeginTransactionAsync();

        var order = await conn.QueryFirstOrDefaultAsync(
            @"SELECT o.id, o.status
              FROM orders o
              JOIN users u ON o.user_id = u.id
              WHERE o.id = @id AND u.email = @email
              FOR UPDATE OF o",
            new { id = orderGuid, email },
            tx
        );
        if (order is null)
        {
            return ModifyOrderResult.Failure($"Order not found or access denied: {orderId}");
        }

        var currentStatus = (string)order.status;
        if (!ModifiableStatuses.Contains(currentStatus))
        {
            return new ModifyOrderResult(
                Error:
                    $"Cannot modify order in '{currentStatus}' status. Only 'placed' or 'confirmed' orders can be modified.",
                OrderId: ((Guid)order.id).ToString(),
                CurrentStatus: currentStatus,
                NewAddress: null,
                Message: null
            );
        }

        var addressJson = JsonSerializer.Serialize(newAddress);
        // Cast text → jsonb so the column accepts the document.
        var sql = "UPDATE orders SET shipping_address = @json::jsonb WHERE id = @id";
        var cmd = new NpgsqlCommand(sql, (NpgsqlConnection)conn, (NpgsqlTransaction)tx);
        cmd.Parameters.Add(new NpgsqlParameter("@json", addressJson));
        cmd.Parameters.Add(new NpgsqlParameter("@id", orderGuid));
        await cmd.ExecuteNonQueryAsync();
        await conn.ExecuteAsync(
            @"INSERT INTO order_status_history (order_id, status, notes)
              VALUES (@id, @status, 'Shipping address updated by customer')",
            new { id = orderGuid, status = currentStatus },
            tx
        );
        await tx.CommitAsync();

        return new ModifyOrderResult(
            Error: null,
            OrderId: ((Guid)order.id).ToString(),
            CurrentStatus: null,
            NewAddress: newAddress,
            Message: $"Shipping address updated for order {orderGuid}."
        );
    }
}

// ─────────────────────── DTOs ───────────────────────

public sealed record OrderSummary(
    string OrderId,
    string Status,
    decimal Total,
    decimal DiscountAmount,
    string? CouponCode,
    string? ShippingCarrier,
    string? TrackingNumber,
    int ItemCount,
    string CreatedAt
);

public sealed record OrderItem(
    string ItemId,
    string ProductName,
    string Category,
    string Brand,
    int Quantity,
    decimal UnitPrice,
    decimal Subtotal
);

public sealed record OrderStatusEntry(string Status, string? Notes, string? Location, string Timestamp);

public sealed record OrderDetails(
    string OrderId,
    string Status,
    decimal Total,
    decimal DiscountAmount,
    string? CouponCode,
    Dictionary<string, JsonElement>? ShippingAddress,
    string? ShippingCarrier,
    string? TrackingNumber,
    string CreatedAt,
    List<OrderItem> Items,
    List<OrderStatusEntry> StatusHistory
);

public sealed record OrderTracking(
    string OrderId,
    string Status,
    string? ShippingCarrier,
    string? TrackingNumber,
    OrderStatusEntry? LatestUpdate,
    List<OrderStatusEntry> Timeline,
    string? Message
);

public sealed record CancelOrderResult(
    string? Error,
    string OrderId,
    string? CurrentStatus,
    string? PreviousStatus,
    string? NewStatus,
    string? Reason,
    decimal? RefundAmount,
    string? Message
)
{
    public static CancelOrderResult Failure(string error) =>
        new(error, "", null, null, null, null, null, null);
}

public sealed record ModifyOrderResult(
    string? Error,
    string OrderId,
    string? CurrentStatus,
    ShippingAddressInput? NewAddress,
    string? Message
)
{
    public static ModifyOrderResult Failure(string error) => new(error, "", null, null, null);
}

/// <summary>
/// Strict shape for the new shipping address. Mirrors the Python
/// <c>ShippingAddress</c> Pydantic model (audit fix #10): every postal
/// code on the planet contains at least one digit, state/country are
/// 2–3 char codes.
/// </summary>
public sealed record ShippingAddressInput(
    [property: Description("Street")] string Street,
    [property: Description("City")] string City,
    [property: Description("2- or 3-letter state code")] string State,
    [property: Description("3-12 alphanumerics, dashes or spaces; must contain at least one digit")]
        string Zip,
    [property: Description("2- or 3-letter ISO country code")] string Country
)
{
    private static readonly System.Text.RegularExpressions.Regex ZipPattern =
        new(@"^(?=.*\d)[A-Za-z0-9 \-]{3,12}$", System.Text.RegularExpressions.RegexOptions.Compiled);
    private static readonly System.Text.RegularExpressions.Regex StatePattern =
        new(@"^[A-Za-z]{2,3}$", System.Text.RegularExpressions.RegexOptions.Compiled);

    /// <summary>Returns null if valid, an error message otherwise.</summary>
    public string? Validate()
    {
        if (string.IsNullOrWhiteSpace(Street) || Street.Length > 200)
            return "street must be 1-200 chars";
        if (string.IsNullOrWhiteSpace(City) || City.Length > 100)
            return "city must be 1-100 chars";
        if (!StatePattern.IsMatch(State))
            return "state must be a 2- or 3-letter code";
        if (!ZipPattern.IsMatch(Zip))
            return "zip must be 3-12 alphanumerics with at least one digit";
        if (!StatePattern.IsMatch(Country))
            return "country must be a 2- or 3-letter ISO code";
        return null;
    }
}
