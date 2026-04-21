using System.Text.Json;

namespace ECommerceAgents.Shared.Workflows;

/// <summary>
/// Pre-purchase research workflow — .NET parity port of
/// <c>agents/python/workflows/pre_purchase.py</c>.
/// <para>
/// Fans out three parallel data-gathering tool calls (reviews, stock,
/// price history), waits on the barrier, runs shipping sequentially
/// when stock is confirmed, and synthesizes a one-line recommendation.
/// </para>
/// <para>
/// The .NET port uses <c>Task.WhenAll</c> for the fan-out. The
/// observable contract — input state → populated state → recommendation
/// string — matches the Python implementation exactly. Ch13 of the
/// tutorial series (when it lands) re-expresses this workflow on MAF's
/// <c>WorkflowBuilder</c> native Concurrent primitive.
/// </para>
/// </summary>
public sealed class PrePurchaseWorkflow
{
    private readonly IPrePurchaseTools _tools;

    public PrePurchaseWorkflow(IPrePurchaseTools tools)
    {
        _tools = tools ?? throw new ArgumentNullException(nameof(tools));
    }

    public async Task<ResearchState> ExecuteAsync(ResearchState state, CancellationToken ct = default)
    {
        ArgumentNullException.ThrowIfNull(state);

        // Fan-out: three gatherers in parallel.
        var reviewsTask = RunAsync("reviews", state,
            async s => s.Reviews = await _tools.AnalyzeSentimentAsync(s.ProductId, ct), ct);
        var stockTask = RunAsync("stock", state,
            async s => s.Stock = await _tools.CheckStockAsync(s.ProductId, ct), ct);
        var priceTask = RunAsync("price_history", state,
            async s => s.PriceHistory = await _tools.GetPriceHistoryAsync(s.ProductId, 90, ct), ct);

        await Task.WhenAll(reviewsTask, stockTask, priceTask);

        // Fan-in barrier: shipping depends on stock outcome.
        if (IsInStock(state.Stock))
        {
            await RunAsync("shipping", state,
                async s => s.Shipping = await _tools.EstimateShippingAsync(
                    s.ProductId, s.UserRegion, ct), ct);
        }

        state.Recommendation = BuildRecommendation(state);
        return state;
    }

    private static async Task RunAsync(
        string step,
        ResearchState state,
        Func<ResearchState, Task> body,
        CancellationToken ct
    )
    {
        try
        {
            await body(state);
            state.CompletedSteps.Add(step);
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested)
        {
            throw;
        }
        catch (Exception ex)
        {
            state.Errors.Add($"{step}: {ex.Message}");
        }
    }

    private static bool IsInStock(JsonElement? stock)
    {
        if (stock is null) return false;
        if (stock.Value.ValueKind != JsonValueKind.Object) return false;
        return stock.Value.TryGetProperty("in_stock", out var v)
            && v.ValueKind == JsonValueKind.True;
    }

    private static string BuildRecommendation(ResearchState state)
    {
        var parts = new List<string>();

        if (state.Reviews is { ValueKind: JsonValueKind.Object } r
            && r.TryGetProperty("sentiment", out var sentiment)
            && sentiment.ValueKind == JsonValueKind.String)
        {
            int total = r.TryGetProperty("total_reviews", out var t) && t.TryGetInt32(out var ti) ? ti : 0;
            parts.Add($"Reviews: {sentiment.GetString()} ({total} reviews)");
        }

        if (state.Stock is { ValueKind: JsonValueKind.Object } s
            && s.TryGetProperty("in_stock", out var inStock)
            && inStock.ValueKind == JsonValueKind.True)
        {
            int qty = s.TryGetProperty("total_quantity", out var q) && q.TryGetInt32(out var qi) ? qi : 0;
            parts.Add($"Stock: {qty} units available");
        }
        else
        {
            parts.Add("Stock: Currently out of stock");
        }

        if (state.PriceHistory is { ValueKind: JsonValueKind.Object } p)
        {
            bool isGoodDeal = p.TryGetProperty("is_good_deal", out var g) && g.ValueKind == JsonValueKind.True;
            if (isGoodDeal)
            {
                decimal avg = p.TryGetProperty("average_price", out var a) && a.TryGetDecimal(out var av) ? av : 0m;
                parts.Add($"Price: Good deal (below {avg:F0} avg)");
            }
            else if (p.TryGetProperty("trend", out var trend) && trend.ValueKind == JsonValueKind.String)
            {
                parts.Add($"Price trend: {trend.GetString()}");
            }
        }

        if (state.Shipping is { ValueKind: JsonValueKind.Object } sh
            && sh.TryGetProperty("options", out var options)
            && options.ValueKind == JsonValueKind.Array
            && options.GetArrayLength() > 0)
        {
            var cheapest = options[0];
            decimal price = cheapest.TryGetProperty("price", out var pr) && pr.TryGetDecimal(out var prd) ? prd : 0m;
            string days = cheapest.TryGetProperty("days", out var d) && d.ValueKind != JsonValueKind.Null
                ? d.ToString()
                : "N/A";
            parts.Add($"Shipping: from ${price:F2}, {days} days");
        }

        return parts.Count == 0
            ? "Insufficient data for recommendation"
            : string.Join(" | ", parts);
    }
}
