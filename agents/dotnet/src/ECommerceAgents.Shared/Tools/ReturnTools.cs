using Dapper;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Guardrails;
using Microsoft.Extensions.AI;
using System.ComponentModel;

namespace ECommerceAgents.Shared.Tools;

/// <summary>
/// Return eligibility, initiation, refunds and status — the .NET twin of
/// Python's <c>shared/tools/return_tools.py</c> (#18).
/// </summary>
/// <remarks>
/// The two destructive tools here (<c>InitiateReturn</c>, <c>ProcessRefund</c>)
/// are the ones <see cref="Middleware.HitlGate"/>'s own comment said had "no
/// .NET tool at all, so there's nothing to gate" — that is no longer true, and
/// both are added to its gated set in the same change. Shipping a refund tool
/// without the approval gate would have been the single most dangerous thing
/// in this parity effort.
///
/// Every query is scoped by the caller's own email, so one user cannot return
/// or refund another's order regardless of what id the model passes.
/// </remarks>
public sealed class ReturnTools(DatabasePool pool, AgentSettings settings)
{
    private readonly DatabasePool _pool = pool;
    private readonly AgentSettings _settings = settings;

    private const int ReturnWindowDays = 30;

    public IEnumerable<AITool> All() => new AITool[]
    {
        AIFunctionFactory.Create(CheckReturnEligibility, nameof(CheckReturnEligibility)),
        AIFunctionFactory.Create(InitiateReturn, nameof(InitiateReturn)),
        AIFunctionFactory.Create(ProcessRefund, nameof(ProcessRefund)),
        AIFunctionFactory.Create(GetReturnStatus, nameof(GetReturnStatus)),
    };

    [Description("Check if an order is eligible for return. Orders must be delivered within the last 30 days.")]
    public async Task<object> CheckReturnEligibility(
        [Description("UUID of the order to check")] string orderId
    )
    {
        var email = RequestContext.CurrentUserEmail;
        if (string.IsNullOrEmpty(email))
        {
            return new { error = "No user context available" };
        }
        if (!Guid.TryParse(orderId, out var id))
        {
            return new { error = $"Order not found or access denied: {orderId}" };
        }

        await using var conn = await _pool.OpenAsync();

        var order = await conn.QueryFirstOrDefaultAsync(
            @"SELECT o.id, o.status, o.total
              FROM orders o JOIN users u ON o.user_id = u.id
              WHERE o.id = @id AND u.email = @email",
            new { id, email }
        );
        if (order is null)
        {
            return new { error = $"Order not found or access denied: {orderId}" };
        }

        var status = (string)order.status;
        if (status != "delivered")
        {
            return new
            {
                eligible = false,
                order_id = orderId,
                status,
                reason = $"Order must be in 'delivered' status to initiate a return. Current status: {status}.",
            };
        }

        var existing = await conn.QueryFirstOrDefaultAsync(
            "SELECT id, status FROM returns WHERE order_id = @id", new { id }
        );
        if (existing is not null)
        {
            return new
            {
                eligible = false,
                order_id = orderId,
                reason = $"A return already exists for this order (status: {(string)existing.status}).",
                return_id = ((Guid)existing.id).ToString(),
            };
        }

        var deliveredAt = await conn.ExecuteScalarAsync<DateTime?>(
            @"SELECT timestamp FROM order_status_history
              WHERE order_id = @id AND status = 'delivered'
              ORDER BY timestamp DESC LIMIT 1",
            new { id }
        );

        var daysRemaining = ReturnWindowDays;
        if (deliveredAt is { } delivered)
        {
            var daysSince = (int)(DateTime.UtcNow - DateTime.SpecifyKind(delivered, DateTimeKind.Utc)).TotalDays;
            if (daysSince > ReturnWindowDays)
            {
                return new
                {
                    eligible = false,
                    order_id = orderId,
                    reason = $"Return window expired. Order was delivered {daysSince} days ago ({ReturnWindowDays}-day limit).",
                    delivered_at = delivered.ToString("o"),
                };
            }
            daysRemaining = ReturnWindowDays - daysSince;
        }

        return new
        {
            eligible = true,
            order_id = orderId,
            total = (decimal)order.total,
            days_remaining = daysRemaining,
            message = $"Order is eligible for return. {daysRemaining} days remaining in the return window.",
        };
    }

    [Description("Initiate a return for a delivered order. Generates a return shipping label.")]
    public async Task<object> InitiateReturn(
        [Description("UUID of the order to return")] string orderId,
        [Description("Reason for the return")] string reason,
        [Description("Refund method: 'original_payment' or 'store_credit'")] string refundMethod = "original_payment"
    )
    {
        if (RoleGuard.Ensure(_settings, "customer", "seller") is { } denied)
        {
            return new { error = denied };
        }

        var email = RequestContext.CurrentUserEmail;
        if (string.IsNullOrEmpty(email))
        {
            return new { error = "No user context available" };
        }
        if (!Guid.TryParse(orderId, out var id))
        {
            return new { error = $"Order not found or access denied: {orderId}" };
        }
        if (refundMethod is not ("original_payment" or "store_credit"))
        {
            return new { error = "refund_method must be 'original_payment' or 'store_credit'" };
        }

        // Re-check eligibility rather than trusting the model to have called
        // CheckReturnEligibility first — an approval gate in front of an
        // unchecked mutation would gate the wrong thing.
        var eligibility = await CheckReturnEligibility(orderId);
        if (eligibility.GetType().GetProperty("eligible")?.GetValue(eligibility) is not true)
        {
            return eligibility;
        }

        await using var conn = await _pool.OpenAsync();

        // ON CONFLICT DO NOTHING against the one-return-per-order guard, so a
        // retry can't create a second return row. .NET has no idempotency
        // store yet (#30's backstop is still open), which makes the database
        // constraint the only thing standing between a double-click and two
        // refunds.
        var returnId = await conn.QueryFirstOrDefaultAsync<Guid?>(
            @"INSERT INTO returns (order_id, user_id, reason, status, refund_method, refund_amount)
              SELECT o.id, o.user_id, @reason, 'requested', @refundMethod, o.total
              FROM orders o JOIN users u ON o.user_id = u.id
              WHERE o.id = @id AND u.email = @email
                AND NOT EXISTS (SELECT 1 FROM returns r WHERE r.order_id = o.id)
              RETURNING id",
            new { id, email, reason, refundMethod }
        );

        if (returnId is null)
        {
            return new { error = "Return could not be created — it may already exist for this order." };
        }

        var refundAmount = await conn.ExecuteScalarAsync<decimal>(
            "SELECT refund_amount FROM returns WHERE id = @rid", new { rid = returnId.Value }
        );

        return new
        {
            return_id = returnId.Value.ToString(),
            order_id = orderId,
            status = "requested",
            refund_method = refundMethod,
            refund_amount = refundAmount,
            message = "Return initiated. A return shipping label will be issued.",
        };
    }

    [Description("Process the refund for an approved return.")]
    public async Task<object> ProcessRefund(
        [Description("UUID of the return to refund")] string returnId
    )
    {
        if (RoleGuard.Ensure(_settings, "customer", "seller") is { } denied)
        {
            return new { error = denied };
        }

        var email = RequestContext.CurrentUserEmail;
        if (string.IsNullOrEmpty(email))
        {
            return new { error = "No user context available" };
        }
        if (!Guid.TryParse(returnId, out var id))
        {
            return new { error = $"Return not found: {returnId}" };
        }

        await using var conn = await _pool.OpenAsync();

        // Guarded on the current status inside the UPDATE, so two concurrent
        // calls cannot both refund — the same claim-before-acting shape #28
        // established for HITL approvals.
        var refunded = await conn.QueryFirstOrDefaultAsync(
            @"UPDATE returns r
              SET status = 'refunded', resolved_at = NOW()
              FROM orders o, users u
              WHERE r.id = @id AND r.order_id = o.id AND o.user_id = u.id
                AND u.email = @email AND r.status <> 'refunded'
              RETURNING r.id, r.refund_amount, r.refund_method",
            new { id, email }
        );

        if (refunded is null)
        {
            return new { error = "Return not found, not yours, or already refunded." };
        }

        return new
        {
            return_id = returnId,
            status = "refunded",
            refund_amount = (decimal)refunded.refund_amount,
            refund_method = (string)refunded.refund_method,
            message = "Refund processed.",
        };
    }

    [Description("Get the status of a return.")]
    public async Task<object> GetReturnStatus(
        [Description("UUID of the return")] string returnId
    )
    {
        var email = RequestContext.CurrentUserEmail;
        if (string.IsNullOrEmpty(email))
        {
            return new { error = "No user context available" };
        }
        if (!Guid.TryParse(returnId, out var id))
        {
            return new { error = $"Return not found: {returnId}" };
        }

        await using var conn = await _pool.OpenAsync();
        var row = await conn.QueryFirstOrDefaultAsync(
            @"SELECT r.id, r.order_id, r.status, r.reason, r.refund_method,
                     r.refund_amount, r.created_at, r.resolved_at
              FROM returns r
              JOIN orders o ON r.order_id = o.id
              JOIN users u ON o.user_id = u.id
              WHERE r.id = @id AND u.email = @email",
            new { id, email }
        );

        if (row is null)
        {
            return new { error = $"Return not found: {returnId}" };
        }

        return new
        {
            return_id = ((Guid)row.id).ToString(),
            order_id = ((Guid)row.order_id).ToString(),
            status = (string)row.status,
            reason = (string)row.reason,
            refund_method = (string)row.refund_method,
            refund_amount = row.refund_amount is null ? 0m : (decimal)row.refund_amount,
            created_at = ((DateTime)row.created_at).ToString("o"),
            resolved_at = row.resolved_at is null ? null : ((DateTime)row.resolved_at).ToString("o"),
        };
    }
}
