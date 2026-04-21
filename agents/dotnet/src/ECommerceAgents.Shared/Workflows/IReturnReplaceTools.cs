using System.Text.Json;

namespace ECommerceAgents.Shared.Workflows;

/// <summary>
/// Tools the return-and-replace workflow depends on. Kept narrow so
/// tests can stub each piece independently.
/// </summary>
public interface IReturnReplaceTools
{
    Task<ReturnEligibility> CheckReturnEligibilityAsync(string orderId, CancellationToken ct = default);

    Task<InitiateReturnResult> InitiateReturnAsync(
        string orderId,
        string reason,
        string refundMethod,
        CancellationToken ct = default
    );

    Task<IReadOnlyList<JsonElement>> SearchReplacementsAsync(
        decimal maxPrice,
        decimal minRating,
        int limit,
        CancellationToken ct = default
    );

    /// <summary>
    /// Returns the caller's loyalty tier info, or null when the tool is
    /// unavailable / the user has no tier.
    /// </summary>
    Task<LoyaltyInfo?> GetLoyaltyTierAsync(CancellationToken ct = default);
}

public sealed record ReturnEligibility(bool Eligible, string? Reason = null);

public sealed record InitiateReturnResult(
    string? ReturnId,
    decimal RefundAmount,
    string? Error = null
);

public sealed record LoyaltyInfo(string? Tier, decimal DiscountPct);
