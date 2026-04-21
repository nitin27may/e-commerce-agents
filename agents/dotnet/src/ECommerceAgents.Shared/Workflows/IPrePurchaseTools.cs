using System.Text.Json;

namespace ECommerceAgents.Shared.Workflows;

/// <summary>
/// Narrow interface the pre-purchase workflow calls. Each method wraps
/// a specialist-agent tool; returns a <see cref="JsonElement"/> so the
/// workflow preserves the Python tool payload shape verbatim.
/// </summary>
public interface IPrePurchaseTools
{
    Task<JsonElement> AnalyzeSentimentAsync(string productId, CancellationToken ct = default);
    Task<JsonElement> CheckStockAsync(string productId, CancellationToken ct = default);
    Task<JsonElement> GetPriceHistoryAsync(string productId, int days, CancellationToken ct = default);
    Task<JsonElement> EstimateShippingAsync(string productId, string destinationRegion, CancellationToken ct = default);
}
