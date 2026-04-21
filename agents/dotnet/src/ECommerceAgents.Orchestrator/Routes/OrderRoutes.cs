using Dapper;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Routing;
using System.Text.Json;

namespace ECommerceAgents.Orchestrator.Routes;

/// <summary>
/// <c>/api/orders</c> endpoints — list, detail, cancel, return.
/// Mirrors the Python equivalents 1:1 including the state transitions
/// on cancel / return (transactional, status-gated).
/// </summary>
public static class OrderRoutes
{
    public sealed record CancelRequest(string Reason);
    public sealed record ReturnRequest(string Reason, string? RefundMethod = "original_payment");

    public static IEndpointRouteBuilder MapOrderRoutes(this IEndpointRouteBuilder routes)
    {
        routes.MapGet("/api/orders", ListOrders);
        routes.MapGet("/api/orders/{id}", GetOrder);
        routes.MapPost("/api/orders/{id}/cancel", CancelOrder);
        routes.MapPost("/api/orders/{id}/return", ReturnOrder);
        return routes;
    }

    // ─────────────────────── list ────────────────────────────

    private static async Task<IResult> ListOrders(
        DatabasePool pool,
        string? status = null,
        int limit = 20,
        int offset = 0
    )
    {
        var email = RequestContext.CurrentUserEmail;
        if (string.IsNullOrEmpty(email)) return Results.Unauthorized();

        var clampedLimit = Math.Clamp(limit, 1, 200);
        var clampedOffset = Math.Max(0, offset);

        await using var conn = await pool.OpenAsync();
        var rows = (await conn.QueryAsync(
            @"SELECT o.id, o.status, o.total, o.shipping_carrier, o.tracking_number,
                     o.created_at, COUNT(oi.id) AS item_count
              FROM orders o
              JOIN users u ON o.user_id = u.id
              LEFT JOIN order_items oi ON oi.order_id = o.id
              WHERE u.email = @email
                AND (@status::text IS NULL OR o.status = @status)
              GROUP BY o.id
              ORDER BY o.created_at DESC
              LIMIT @limit OFFSET @offset",
            new { email, status, limit = clampedLimit, offset = clampedOffset }
        )).Select(r => new
        {
            id = ((Guid)r.id).ToString(),
            status = (string)r.status,
            total = (decimal)r.total,
            carrier = (string?)r.shipping_carrier,
            tracking = (string?)r.tracking_number,
            item_count = Convert.ToInt32(r.item_count),
            date = ((DateTime)r.created_at).ToString("o"),
        }).ToList();

        var total = await conn.ExecuteScalarAsync<int>(
            @"SELECT COUNT(*) FROM orders o
              JOIN users u ON o.user_id = u.id
              WHERE u.email = @email AND (@status::text IS NULL OR o.status = @status)",
            new { email, status }
        );
        return Results.Ok(new { orders = rows, total });
    }

    // ─────────────────────── detail ──────────────────────────

    private static async Task<IResult> GetOrder(string id, DatabasePool pool)
    {
        if (!Guid.TryParse(id, out var orderId))
        {
            return Results.NotFound(new { detail = "Order not found" });
        }
        var email = RequestContext.CurrentUserEmail;
        if (string.IsNullOrEmpty(email)) return Results.Unauthorized();

        await using var conn = await pool.OpenAsync();
        var order = await conn.QueryFirstOrDefaultAsync(
            @"SELECT o.id, o.status, o.total, o.shipping_address, o.billing_address,
                     o.shipping_carrier, o.tracking_number, o.coupon_code,
                     o.discount_amount, o.created_at
              FROM orders o
              JOIN users u ON o.user_id = u.id
              WHERE o.id = @id AND u.email = @email",
            new { id = orderId, email }
        );
        if (order is null)
        {
            return Results.NotFound(new { detail = "Order not found" });
        }

        var items = (await conn.QueryAsync(
            @"SELECT oi.quantity, oi.unit_price, oi.subtotal,
                     p.id AS product_id, p.name, p.category, p.image_url
              FROM order_items oi
              JOIN products p ON oi.product_id = p.id
              WHERE oi.order_id = @id",
            new { id = orderId }
        )).Select(i => new
        {
            product_id = ((Guid)i.product_id).ToString(),
            name = (string)i.name,
            category = (string)i.category,
            image_url = (string?)i.image_url,
            quantity = (int)i.quantity,
            unit_price = (decimal)i.unit_price,
            subtotal = (decimal)i.subtotal,
        }).ToList();

        var history = (await conn.QueryAsync(
            "SELECT status, notes, location, timestamp FROM order_status_history WHERE order_id = @id ORDER BY timestamp",
            new { id = orderId }
        )).Select(h => new
        {
            status = (string)h.status,
            notes = (string?)h.notes,
            location = (string?)h.location,
            timestamp = ((DateTime)h.timestamp).ToString("o"),
        }).ToList();

        var ret = await conn.QueryFirstOrDefaultAsync(
            @"SELECT id, reason, status, refund_method, refund_amount, return_label_url,
                     created_at, resolved_at
              FROM returns WHERE order_id = @id",
            new { id = orderId }
        );

        return Results.Ok(new
        {
            id = ((Guid)order.id).ToString(),
            status = (string)order.status,
            total = (decimal)order.total,
            shipping_address = ParseJson(order.shipping_address),
            billing_address = ParseJson(order.billing_address),
            carrier = (string?)order.shipping_carrier,
            tracking = (string?)order.tracking_number,
            coupon = (string?)order.coupon_code,
            discount = order.discount_amount is null ? 0m : (decimal)order.discount_amount,
            date = ((DateTime)order.created_at).ToString("o"),
            items,
            status_history = history,
            @return = ret is null ? null : new
            {
                id = ((Guid)ret.id).ToString(),
                reason = (string?)ret.reason,
                status = (string)ret.status,
                refund_method = (string?)ret.refund_method,
                refund_amount = ret.refund_amount is null ? 0m : (decimal)ret.refund_amount,
                label_url = (string?)ret.return_label_url,
                created_at = ((DateTime)ret.created_at).ToString("o"),
                resolved_at = ret.resolved_at is null ? null : ((DateTime)ret.resolved_at).ToString("o"),
            },
        });
    }

    // ─────────────────────── cancel ──────────────────────────

    private static async Task<IResult> CancelOrder(
        string id,
        [FromBody] CancelRequest body,
        DatabasePool pool
    )
    {
        if (!Guid.TryParse(id, out var orderId))
        {
            return Results.NotFound(new { detail = "Order not found" });
        }
        var email = RequestContext.CurrentUserEmail;
        if (string.IsNullOrEmpty(email)) return Results.Unauthorized();
        if (string.IsNullOrWhiteSpace(body?.Reason))
        {
            return Results.BadRequest(new { detail = "reason is required" });
        }

        await using var conn = await pool.OpenAsync();
        await using var tx = await conn.BeginTransactionAsync();
        var order = await conn.QueryFirstOrDefaultAsync(
            @"SELECT o.id, o.status, o.total
              FROM orders o
              JOIN users u ON o.user_id = u.id
              WHERE o.id = @id AND u.email = @email
              FOR UPDATE OF o",
            new { id = orderId, email },
            tx
        );
        if (order is null)
        {
            return Results.NotFound(new { detail = "Order not found" });
        }

        var currentStatus = (string)order.status;
        if (currentStatus is not "placed" and not "confirmed")
        {
            return Results.BadRequest(new
            {
                detail = $"Cannot cancel order with status '{currentStatus}'. Only placed or confirmed orders can be cancelled.",
            });
        }

        await conn.ExecuteAsync(
            "UPDATE orders SET status = 'cancelled' WHERE id = @id",
            new { id = orderId },
            tx
        );
        await conn.ExecuteAsync(
            @"INSERT INTO order_status_history (order_id, status, notes)
              VALUES (@id, 'cancelled', @notes)",
            new { id = orderId, notes = body.Reason },
            tx
        );
        await tx.CommitAsync();

        return Results.Ok(new
        {
            order_id = ((Guid)order.id).ToString(),
            status = "cancelled",
            refund_amount = (decimal)order.total,
        });
    }

    // ─────────────────────── return ──────────────────────────

    private static async Task<IResult> ReturnOrder(
        string id,
        [FromBody] ReturnRequest body,
        DatabasePool pool
    )
    {
        if (!Guid.TryParse(id, out var orderId))
        {
            return Results.NotFound(new { detail = "Order not found" });
        }
        var email = RequestContext.CurrentUserEmail;
        if (string.IsNullOrEmpty(email)) return Results.Unauthorized();
        if (string.IsNullOrWhiteSpace(body?.Reason))
        {
            return Results.BadRequest(new { detail = "reason is required" });
        }

        var refundMethod = body.RefundMethod ?? "original_payment";
        if (refundMethod is not "original_payment" and not "store_credit")
        {
            return Results.BadRequest(new { detail = "refund_method must be 'original_payment' or 'store_credit'" });
        }

        await using var conn = await pool.OpenAsync();
        await using var tx = await conn.BeginTransactionAsync();
        var order = await conn.QueryFirstOrDefaultAsync(
            @"SELECT o.id, o.status, o.total, o.user_id
              FROM orders o
              JOIN users u ON o.user_id = u.id
              WHERE o.id = @id AND u.email = @email
              FOR UPDATE OF o",
            new { id = orderId, email },
            tx
        );
        if (order is null)
        {
            return Results.NotFound(new { detail = "Order not found" });
        }

        var currentStatus = (string)order.status;
        if (currentStatus != "delivered")
        {
            return Results.BadRequest(new
            {
                detail = $"Cannot return order with status '{currentStatus}'. Only delivered orders can be returned.",
            });
        }

        var existing = await conn.QueryFirstOrDefaultAsync<Guid?>(
            "SELECT id FROM returns WHERE order_id = @id",
            new { id = orderId },
            tx
        );
        if (existing is not null)
        {
            return Results.Conflict(new { detail = "A return has already been requested for this order" });
        }

        var labelToken = Guid.NewGuid().ToString("N")[..12];
        var labelUrl = $"/api/returns/{labelToken}/label";
        var total = (decimal)order.total;
        var returnId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO returns (order_id, user_id, reason, status, return_label_url, refund_method, refund_amount)
              VALUES (@oid, @uid, @reason, 'requested', @label, @method, @total)
              RETURNING id",
            new
            {
                oid = orderId,
                uid = (Guid)order.user_id,
                reason = body.Reason,
                label = labelUrl,
                method = refundMethod,
                total,
            },
            tx
        );
        await conn.ExecuteAsync(
            "UPDATE orders SET status = 'returned' WHERE id = @id",
            new { id = orderId },
            tx
        );
        await conn.ExecuteAsync(
            @"INSERT INTO order_status_history (order_id, status, notes)
              VALUES (@id, 'returned', @notes)",
            new { id = orderId, notes = body.Reason },
            tx
        );
        await tx.CommitAsync();

        return Results.Ok(new
        {
            return_id = returnId.ToString(),
            order_id = ((Guid)order.id).ToString(),
            status = "requested",
            return_label_url = labelUrl,
            refund_amount = total,
            refund_method = refundMethod,
        });
    }

    // ─────────────────────── helpers ─────────────────────────

    private static Dictionary<string, JsonElement> ParseJson(object? raw)
    {
        if (raw is null) return new Dictionary<string, JsonElement>();
        var text = raw is string s ? s : raw.ToString();
        if (string.IsNullOrWhiteSpace(text)) return new Dictionary<string, JsonElement>();
        try
        {
            return JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(text!)
                ?? new Dictionary<string, JsonElement>();
        }
        catch
        {
            return new Dictionary<string, JsonElement>();
        }
    }
}
