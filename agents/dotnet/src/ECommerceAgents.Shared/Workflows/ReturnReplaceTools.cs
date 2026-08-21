using Dapper;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Tools;
using System.Text.Json;

namespace ECommerceAgents.Shared.Workflows;

/// <summary>
/// The production <see cref="IReturnReplaceTools"/> — the other half that was
/// missing when <see cref="ReturnAndReplaceWorkflow"/> was declared "done".
/// </summary>
/// <remarks>
/// Like <see cref="PrePurchaseTools"/>, this interface had no implementation
/// anywhere in <c>src/</c> until now — only a stub in
/// <c>ReturnAndReplaceWorkflowTests</c>. The workflow's graph, its
/// <c>RequestPort</c> HITL gate and its resume path were all real and tested
/// against a fake, and unreachable in production.
///
/// It composes <see cref="ReturnTools"/> and <see cref="LoyaltyTools"/> rather
/// than repeating their SQL, so the workflow and the chat tools can't drift on
/// what "eligible" or "gold tier" means.
/// </remarks>
public sealed class ReturnReplaceTools(DatabasePool pool, AgentSettings settings) : IReturnReplaceTools
{
    private readonly DatabasePool _pool = pool;
    private readonly ReturnTools _returns = new(pool, settings);
    private readonly LoyaltyTools _loyalty = new(pool);

    private static JsonElement Json(object value) => JsonSerializer.SerializeToElement(value);

    private static bool TryRead(JsonElement e, string name, out JsonElement value)
    {
        value = default;
        return e.ValueKind == JsonValueKind.Object && e.TryGetProperty(name, out value);
    }

    public async Task<ReturnEligibility> CheckReturnEligibilityAsync(string orderId, CancellationToken ct = default)
    {
        var result = Json(await _returns.CheckReturnEligibility(orderId));

        if (TryRead(result, "error", out var error))
        {
            return new ReturnEligibility(false, error.GetString());
        }

        var eligible = TryRead(result, "eligible", out var e) && e.ValueKind == JsonValueKind.True;
        var reason = TryRead(result, "reason", out var r) ? r.GetString() : null;
        return new ReturnEligibility(eligible, reason);
    }

    public async Task<InitiateReturnResult> InitiateReturnAsync(
        string orderId,
        string reason,
        string refundMethod,
        CancellationToken ct = default
    )
    {
        var result = Json(await _returns.InitiateReturn(orderId, reason, refundMethod));

        if (TryRead(result, "error", out var error))
        {
            return new InitiateReturnResult(null, 0m, error.GetString());
        }

        var returnId = TryRead(result, "return_id", out var rid) ? rid.GetString() : null;
        var amount = TryRead(result, "refund_amount", out var amt) && amt.TryGetDecimal(out var parsed)
            ? parsed
            : 0m;

        return new InitiateReturnResult(returnId, amount);
    }

    public async Task<IReadOnlyList<JsonElement>> SearchReplacementsAsync(
        decimal maxPrice,
        decimal minRating,
        int limit,
        CancellationToken ct = default
    )
    {
        await using var conn = await _pool.OpenAsync();
        var rows = await conn.QueryAsync(
            @"SELECT id, name, category, brand, price, rating, review_count, image_url
              FROM products
              WHERE is_active = TRUE AND price <= @maxPrice AND rating >= @minRating
              ORDER BY rating DESC, review_count DESC
              LIMIT @limit",
            new { maxPrice, minRating, limit = Math.Clamp(limit, 1, 20) }
        );

        return rows.Select(r => Json(new
        {
            id = ((Guid)r.id).ToString(),
            name = (string)r.name,
            category = (string)r.category,
            brand = (string)r.brand,
            price = (decimal)r.price,
            rating = r.rating is null ? 0m : (decimal)r.rating,
            review_count = r.review_count is null ? 0 : (int)r.review_count,
            image_url = r.image_url as string,
        })).ToList();
    }

    public async Task<LoyaltyInfo?> GetLoyaltyTierAsync(CancellationToken ct = default)
    {
        var result = Json(await _loyalty.GetLoyaltyTier());

        if (TryRead(result, "error", out _))
        {
            // Null rather than a zero-discount default: the workflow treats
            // "no tier information" and "bronze, 0%" differently, and silently
            // collapsing them would quietly deny a gold customer their
            // discount on a replacement.
            return null;
        }

        var tier = TryRead(result, "tier", out var t) ? t.GetString() : null;
        var pct = TryRead(result, "discount_pct", out var d) && d.TryGetDecimal(out var parsed) ? parsed : 0m;
        return new LoyaltyInfo(tier, pct);
    }
}
