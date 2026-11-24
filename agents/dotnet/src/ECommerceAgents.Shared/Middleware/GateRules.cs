using System.Collections.Frozen;

namespace ECommerceAgents.Shared.Middleware;

/// <summary>
/// Business rules shared by a gated tool and its pre-park check (<see cref="ParkPrechecks"/>) — the .NET twin of Python's
/// <c>shared/gate_rules.py</c>.
/// </summary>
/// <remarks>
/// A gated call is stopped by <see cref="HitlGate"/> before the tool's own body runs. The pre-check has to refuse exactly what the
/// tool would refuse, so the status sets, the return window and the plain-words reason for each status live here once and both
/// sides read them. The tools keep their own error payloads; only the decision is shared.
/// </remarks>
public static class GateRules
{
    /// <summary>Order states in which the customer may still cancel or change the address.</summary>
    public static readonly FrozenSet<string> CancellableStatuses = new[] { "placed", "confirmed" }.ToFrozenSet(StringComparer.Ordinal);

    public static readonly FrozenSet<string> ModifiableStatuses = CancellableStatuses;

    /// <summary>The one order state a return may start from.</summary>
    public const string ReturnableStatus = "delivered";

    public const int ReturnWindowDays = 30;

    private static readonly FrozenDictionary<string, string> StatusClauses = new Dictionary<string, string>
    {
        ["shipped"] = "has already shipped",
        ["out_for_delivery"] = "is already out for delivery",
        ["delivered"] = "has already been delivered",
        ["cancelled"] = "has already been cancelled",
        ["returned"] = "has already been returned",
        ["processing"] = "is already being processed",
    }.ToFrozenDictionary(StringComparer.Ordinal);

    /// <summary>Plain-words predicate for an order status ("has already shipped").</summary>
    public static string StatusClause(string status) =>
        StatusClauses.TryGetValue(status, out var clause) ? clause : $"is in '{status}' status";

    /// <summary>Why an order in <paramref name="status"/> cannot be cancelled, or <c>null</c> when it can.</summary>
    public static string? CancelBlocker(string status) => CancellableStatuses.Contains(status) ? null : StatusClause(status);

    /// <summary>Why an order in <paramref name="status"/> cannot have its address changed, or <c>null</c> when it can.</summary>
    public static string? ModifyBlocker(string status) => ModifiableStatuses.Contains(status) ? null : StatusClause(status);

    /// <summary>Why an order in <paramref name="status"/> cannot be returned, or <c>null</c> when it can.</summary>
    public static string? ReturnStatusBlocker(string status) => status == ReturnableStatus ? null : StatusClause(status);

    /// <summary>Why a return in <paramref name="status"/> cannot be refunded, or <c>null</c> when it can.</summary>
    public static string? RefundBlocker(string status) => status switch
    {
        "refunded" => "has already been refunded",
        "denied" => "was denied and cannot be refunded",
        _ => null,
    };

    /// <summary>True when the order was delivered more than the window ago (no delivery record: not expired).</summary>
    public static bool ReturnWindowExpired(int? daysSinceDelivery) => daysSinceDelivery is > ReturnWindowDays;
}
