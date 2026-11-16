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

    /// <summary>
    /// Applies a reviewer's edited refund amount to the return that was just created, for the
    /// caller's own return only (identity comes from the request context, never an argument).
    /// Not an agent tool: no model can call it. Never raises a refund above what it was created for.
    /// </summary>
    Task<AdjustReturnResult> AdjustReturnAmountAsync(string returnId, decimal amount, CancellationToken ct = default);

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

public sealed record AdjustReturnResult(bool Success, string? Error = null);
