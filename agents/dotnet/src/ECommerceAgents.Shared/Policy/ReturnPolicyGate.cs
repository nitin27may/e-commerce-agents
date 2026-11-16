using Dapper;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Workflows;

namespace ECommerceAgents.Shared.Policy;

/// <summary>
/// Where the return workflow's policy gate gets its routing decision. A <c>null</c> answer means
/// "the engine cannot decide" and the gate falls back to <c>ReturnHitlThreshold</c>.
/// </summary>
public interface IReturnPolicy
{
    Task<PolicyDecision?> DecideAsync(WorkflowState state, CancellationToken ct = default);
}

/// <summary>
/// The production <see cref="IReturnPolicy"/>: gathers the facts for the requester (identity from
/// <see cref="RequestContext"/>, never an argument), evaluates kind <c>return_approval</c> and returns the
/// decision. Mirrors Python's <c>default_return_policy</c> + <c>shared/policy/facts.py</c>.
/// </summary>
public sealed class ReturnPolicyGate(ApprovalPolicy policy, DatabasePool pool) : IReturnPolicy
{
    public const string Kind = "return_approval";

    public async Task<PolicyDecision?> DecideAsync(WorkflowState state, CancellationToken ct = default)
    {
        try
        {
            var email = RequestContext.CurrentUserEmail;
            if (string.IsNullOrWhiteSpace(email)) return null;
            var facts = await ApprovalFacts.GatherAsync(pool, email, state.OrderId, (double)state.OrderTotal, ct);
            return policy.Evaluate(Kind, facts);
        }
        catch (Exception ex) when (ex is PolicyException or Npgsql.NpgsqlException or InvalidOperationException)
        {
            // The engine failing must never wedge a customer's return: the gate uses the threshold instead.
            return null;
        }
    }
}

/// <summary>
/// The facts the approval policy reasons over, gathered with parameterised SQL and scoped to the
/// requester's own rows — an order that is not theirs contributes nothing (its facts stay unknown),
/// so this cannot be used to read someone else's order. Same facts and the same queries as Python's
/// <c>shared/policy/facts.py</c>.
/// </summary>
public static class ApprovalFacts
{
    public static async Task<IReadOnlyDictionary<string, object?>> GatherAsync(
        DatabasePool pool,
        string email,
        string? orderId,
        double? amount,
        CancellationToken ct = default,
        DateTime? now = null
    )
    {
        var clock = now ?? DateTime.UtcNow;
        await using var conn = await pool.OpenAsync(ct);

        var user = await conn.QueryFirstOrDefaultAsync(
            "SELECT id, loyalty_tier, total_spend, created_at FROM users WHERE lower(email) = lower(@email)",
            new { email });
        if (user is null)
        {
            return Facts(amount, null, null, null, null, null);
        }

        Guid userId = user.id;
        var returns90d = await conn.ExecuteScalarAsync<long>(
            "SELECT count(*) FROM returns WHERE user_id = @userId AND created_at > @clock::timestamptz - interval '90 days'",
            new { userId, clock });

        int? daysSinceDelivery = null;
        double? orderTotal = null;
        if (orderId is not null && Guid.TryParse(orderId, out var orderGuid))
        {
            var order = await conn.QueryFirstOrDefaultAsync(
                "SELECT id, total FROM orders WHERE id = @orderGuid AND user_id = @userId",
                new { orderGuid, userId });
            if (order is not null)
            {
                orderTotal = (double)(decimal)order.total;
                DateTime? deliveredAt = await conn.ExecuteScalarAsync<DateTime?>(
                    "SELECT max(timestamp) FROM order_status_history WHERE order_id = @orderGuid AND status = 'delivered'",
                    new { orderGuid });
                daysSinceDelivery = WholeDays(deliveredAt, clock);
            }
        }

        var effective = amount ?? orderTotal;
        var spend = user.total_spend is null ? 0d : (double)(decimal)user.total_spend;
        double? ratio = effective is not null && spend > 0
            ? Math.Round(effective.Value / spend, 4, MidpointRounding.AwayFromZero)
            : null;

        return Facts(
            effective,
            daysSinceDelivery,
            (string?)user.loyalty_tier,
            (int)returns90d,
            ratio,
            WholeDays((DateTime?)user.created_at, clock));
    }

    private static int? WholeDays(DateTime? since, DateTime now)
    {
        if (since is null) return null;
        var from = since.Value.Kind == DateTimeKind.Unspecified
            ? DateTime.SpecifyKind(since.Value, DateTimeKind.Utc)
            : since.Value.ToUniversalTime();
        return Math.Max(0, (int)Math.Floor((now.ToUniversalTime() - from).TotalDays));
    }

    private static IReadOnlyDictionary<string, object?> Facts(
        double? amount, int? daysSinceDelivery, string? tier, int? returns90d, double? ratio, int? accountAgeDays) =>
        new Dictionary<string, object?>
        {
            ["amount"] = amount,
            ["days_since_delivery"] = daysSinceDelivery,
            ["loyalty_tier"] = tier,
            ["returns_90d"] = returns90d,
            ["refund_to_spend_ratio"] = ratio,
            ["account_age_days"] = accountAgeDays,
        };
}
