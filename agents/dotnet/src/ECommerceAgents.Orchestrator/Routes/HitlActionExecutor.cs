using Dapper;
using ECommerceAgents.Shared.Approvals;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Idempotency;
using System.Text.Json;

namespace ECommerceAgents.Orchestrator.Routes;

/// <summary>
/// Directly executes a previously admin-approved HITL action without
/// re-running the LLM loop. Mirrors Python's <c>execute_approved_action</c>
/// (<c>shared/hitl.py:254-358</c>) tool_name-keyed dispatch table 1:1 —
/// same DB operations, same success/failure messages. Called by
/// <see cref="HitlRoutes"/>'s approve endpoint.
/// </summary>
internal static class HitlActionExecutor
{
    /// <summary>
    /// Runs an approved destructive action at most once, keyed by the approval's id.
    /// </summary>
    /// <remarks>
    /// The key is the approval id rather than a hash of the input: a reviewer's edits change the input, so an input hash would let an
    /// edited retry run a second time. The id is the same string Python keys on (<c>hitl_execute_approval</c>), so a retry that lands
    /// on the other stack still replays. Returns the result as JSON so a replayed result keeps its types
    /// (<c>success</c> stays a boolean).
    /// </remarks>
    public static async Task<JsonElement> ExecuteApprovedAsync(
        DatabasePool pool,
        string toolName,
        JsonElement toolInput,
        string userEmail,
        Guid approvalId
    )
    {
        var result = await new IdempotencyGuard(pool).RunOnceAsync(
            "hitl_execute_approval",
            approvalId.ToString(),
            async () => (object)await ExecuteCoreAsync(pool, ApprovalIntake.CanonicalToolName(toolName), toolInput, userEmail));

        return result switch
        {
            JsonElement e when e.ValueKind == JsonValueKind.Object => e,
            _ => JsonSerializer.SerializeToElement(result),
        };
    }

    private static async Task<Dictionary<string, object?>> ExecuteCoreAsync(
        DatabasePool pool,
        string toolName,
        JsonElement toolInput,
        string userEmail
    )
    {
        await using var conn = await pool.OpenAsync();

        switch (toolName)
        {
            case "cancel_order":
            {
                var orderId = GetString(toolInput, "order_id") ?? "";
                if (!Guid.TryParse(orderId, out var orderGuid))
                {
                    return Failure("Order not found or already processed.");
                }
                var row = await conn.QueryFirstOrDefaultAsync(
                    @"UPDATE orders SET status = 'cancelled'
                      WHERE id = @id
                        AND user_id = (SELECT id FROM users WHERE email = @email)
                        AND status IN ('placed', 'confirmed')
                      RETURNING id, status, total",
                    new { id = orderGuid, email = userEmail }
                );
                if (row is null)
                {
                    return Failure("Order not found or already processed.");
                }
                var total = (decimal)row.total;
                return Success(new Dictionary<string, object?>
                {
                    ["order_id"] = orderId,
                    ["new_status"] = "cancelled",
                    ["message"] = $"Order {ShortId(orderId)} cancelled. Refund of ${total:F2} initiated.",
                });
            }

            case "process_refund":
            {
                // process_refund takes a return_id (the gated tool's own shape), not an order id: it settles the RETURN, guarded by
                // status so a second execution reports "already resolved" instead of a second success. Same as Python.
                var returnId = GetString(toolInput, "return_id") ?? "";
                if (!Guid.TryParse(returnId, out var returnGuid))
                {
                    return Failure("Return not found, already resolved, or access denied.");
                }
                var row = await conn.QueryFirstOrDefaultAsync(
                    @"UPDATE returns SET status = 'refunded', resolved_at = NOW()
                      WHERE id = @id
                        AND user_id = (SELECT id FROM users WHERE email = @email)
                        AND status NOT IN ('refunded', 'denied')
                      RETURNING id, order_id, refund_method, refund_amount",
                    new { id = returnGuid, email = userEmail }
                );
                if (row is null)
                {
                    return Failure("Return not found, already resolved, or access denied.");
                }
                var amount = row.refund_amount is null ? 0m : (decimal)row.refund_amount;
                var method = (string?)row.refund_method ?? "original_payment";
                return Success(new Dictionary<string, object?>
                {
                    ["return_id"] = returnId,
                    ["order_id"] = ((Guid)row.order_id).ToString(),
                    ["refunded_amount"] = amount,
                    ["message"] = $"Refund of ${amount:F2} processed via {method.Replace('_', ' ')}.",
                });
            }

            case "initiate_return":
            {
                var orderId = GetString(toolInput, "order_id") ?? "";
                var reason = GetString(toolInput, "reason") ?? "Admin-approved return";
                var refundMethod = GetString(toolInput, "refund_method");
                if (refundMethod is not ("original_payment" or "store_credit")) refundMethod = "original_payment";
                if (!Guid.TryParse(orderId, out var orderGuid))
                {
                    return Failure("Order not found.");
                }
                // NOT EXISTS: a second run must not insert a second returns row for the same order.
                var returnId = await conn.QueryFirstOrDefaultAsync<Guid?>(
                    @"INSERT INTO returns (order_id, user_id, reason, status, refund_method)
                      SELECT o.id, o.user_id, @reason, 'approved', @refundMethod
                      FROM orders o JOIN users u ON o.user_id = u.id
                      WHERE o.id = @id AND u.email = @email
                        AND NOT EXISTS (SELECT 1 FROM returns r WHERE r.order_id = o.id)
                      RETURNING id",
                    new { id = orderGuid, email = userEmail, reason, refundMethod }
                );
                if (returnId is null)
                {
                    var existing = await conn.ExecuteScalarAsync<Guid?>(
                        "SELECT id FROM returns WHERE order_id = @id LIMIT 1", new { id = orderGuid });
                    return existing is { } found
                        ? new Dictionary<string, object?>
                        {
                            ["success"] = false,
                            ["return_id"] = found.ToString(),
                            ["message"] = "A return has already been initiated for this order.",
                        }
                        : Failure("Order not found.");
                }
                return Success(new Dictionary<string, object?>
                {
                    ["return_id"] = returnId.Value.ToString(),
                    ["message"] = "Return approved and initiated.",
                });
            }

            case "modify_order":
            {
                var orderId = GetString(toolInput, "order_id") ?? "";
                if (!Guid.TryParse(orderId, out var orderGuid))
                {
                    return Failure("Order not found or already shipped.");
                }
                var addressJson = toolInput.ValueKind == JsonValueKind.Object
                    && toolInput.TryGetProperty("new_address", out var addr)
                        ? addr.GetRawText()
                        : "{}";
                var affected = await conn.ExecuteAsync(
                    @"UPDATE orders SET shipping_address = @json::jsonb
                      WHERE id = @id
                        AND user_id = (SELECT id FROM users WHERE email = @email)
                        AND status NOT IN ('shipped', 'delivered', 'cancelled')",
                    new { id = orderGuid, email = userEmail, json = addressJson }
                );
                if (affected == 0)
                {
                    return Failure("Order not found or already shipped.");
                }
                return Success(new Dictionary<string, object?>
                {
                    ["order_id"] = orderId,
                    ["message"] = "Shipping address updated.",
                });
            }

            case "place_backorder":
            {
                var productId = GetString(toolInput, "product_id") ?? "unknown";
                return Success(new Dictionary<string, object?>
                {
                    ["message"] = $"Backorder approved for product {ShortId(productId)}.",
                });
            }

            default:
                return Failure($"Auto-execution not configured for tool: {toolName}");
        }
    }

    private static string ShortId(string id) => id.Length > 8 ? id[..8] : id;

    private static string? GetString(JsonElement input, string key) =>
        input.ValueKind == JsonValueKind.Object
        && input.TryGetProperty(key, out var v)
        && v.ValueKind == JsonValueKind.String
            ? v.GetString()
            : null;

    private static Dictionary<string, object?> Success(Dictionary<string, object?> extra)
    {
        extra["success"] = true;
        return extra;
    }

    private static Dictionary<string, object?> Failure(string message) =>
        new() { ["success"] = false, ["message"] = message };
}
