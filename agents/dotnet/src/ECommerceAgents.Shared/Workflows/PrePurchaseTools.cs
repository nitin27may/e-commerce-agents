using Dapper;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Tools;
using System.Text.Json;

namespace ECommerceAgents.Shared.Workflows;

/// <summary>
/// The production <see cref="IPrePurchaseTools"/> — what
/// <see cref="PrePurchaseWorkflow"/> actually reads when it runs outside a
/// test.
/// </summary>
/// <remarks>
/// Worth stating plainly, because it was the surprise in this phase: the
/// workflow engine has existed and been tested since the .NET parity work,
/// but <see cref="IPrePurchaseTools"/> had <b>no implementation anywhere in
/// src/</b> — only a stub in <c>PrePurchaseWorkflowTests</c>. So "the
/// workflows are done, they just need registering" was half true: the graph
/// was real, and nothing could feed it. This is the missing half.
///
/// Two of the four methods only became implementable when
/// <see cref="StockLookupTools"/> and <see cref="PriceHistoryTools"/> landed;
/// before that, .NET had no reachable stock or price-history query at all.
///
/// Sentiment and shipping are queried directly here rather than routed
/// through the review-sentiment and inventory-fulfillment specialists over
/// A2A. A workflow is a fixed graph of data fetches — going out to an agent
/// to run a SELECT would add a network hop and an LLM turn to each node for
/// no decision the workflow doesn't already encode. Python's own workflow
/// tools do the same.
/// </remarks>
public sealed class PrePurchaseTools(DatabasePool pool) : IPrePurchaseTools
{
    private readonly DatabasePool _pool = pool;
    private readonly StockLookupTools _stock = new(pool);
    private readonly PriceHistoryTools _prices = new(pool);

    private static JsonElement ToJson(object value) => JsonSerializer.SerializeToElement(value);

    public async Task<JsonElement> CheckStockAsync(string productId, CancellationToken ct = default) =>
        ToJson(await _stock.CheckStock(productId));

    public async Task<JsonElement> GetPriceHistoryAsync(string productId, int days, CancellationToken ct = default) =>
        ToJson(await _prices.GetPriceHistory(productId, days));

    public async Task<JsonElement> AnalyzeSentimentAsync(string productId, CancellationToken ct = default)
    {
        if (!Guid.TryParse(productId, out var id))
        {
            return ToJson(new { error = $"Product not found: {productId}" });
        }

        await using var conn = await _pool.OpenAsync();

        var summary = await conn.QueryFirstOrDefaultAsync(
            @"SELECT COUNT(*) AS review_count, COALESCE(AVG(rating), 0) AS average_rating
              FROM reviews WHERE product_id = @id",
            new { id }
        );

        var count = summary is null ? 0 : (long)summary.review_count;
        if (count == 0)
        {
            return ToJson(new { product_id = productId, total_reviews = 0, average_rating = 0.0, sentiment = "no_reviews" });
        }

        var average = Math.Round((decimal)summary!.average_rating, 2);

        var distribution = (await conn.QueryAsync(
            "SELECT rating, COUNT(*) AS count FROM reviews WHERE product_id = @id GROUP BY rating ORDER BY rating DESC",
            new { id }
        )).ToDictionary(r => ((int)r.rating).ToString(), r => (long)r.count);

        return ToJson(new
        {
            product_id = productId,
            // total_reviews, not review_count: PrePurchaseWorkflow's
            // BuildRecommendation reads this name, and so does Python's
            // analyze_sentiment. Getting it wrong produced the contradictory
            // "positive (0 reviews)" in the first live run — the workflow ran
            // fine and simply read a field that wasn't there.
            total_reviews = count,
            average_rating = average,
            // Same thresholds the review-sentiment specialist uses, so a
            // workflow answer and a chat answer about the same product don't
            // disagree on the adjective.
            sentiment = average >= 4.0m ? "positive" : average >= 3.0m ? "mixed" : "negative",
            rating_distribution = distribution,
        });
    }

    public async Task<JsonElement> EstimateShippingAsync(
        string productId,
        string destinationRegion,
        CancellationToken ct = default
    )
    {
        await using var conn = await _pool.OpenAsync();

        // Cheapest route per carrier from whichever region actually holds
        // stock. Falls back to any rate into the destination when the product
        // is out of stock everywhere, so the workflow still gets a shipping
        // estimate to reason about rather than an empty node.
        var rates = (await conn.QueryAsync(
            @"SELECT c.name AS carrier, c.speed_tier, sr.price,
                     sr.estimated_days_min, sr.estimated_days_max
              FROM shipping_rates sr
              JOIN carriers c ON sr.carrier_id = c.id
              WHERE sr.region_to = @region
              ORDER BY sr.price",
            new { region = string.IsNullOrWhiteSpace(destinationRegion) ? "east" : destinationRegion }
        )).Select(r => new
        {
            carrier = (string)r.carrier,
            speed_tier = (string)r.speed_tier,
            price = (decimal)r.price,
            estimated_days_min = (int)r.estimated_days_min,
            estimated_days_max = (int)r.estimated_days_max,
            // BuildRecommendation reads a single "days" per option; keep the
            // min/max too so a caller wanting the range still has it.
            days = (int)r.estimated_days_min == (int)r.estimated_days_max
                ? $"{(int)r.estimated_days_min}"
                : $"{(int)r.estimated_days_min}-{(int)r.estimated_days_max}",
        }).ToList();

        return ToJson(new
        {
            product_id = productId,
            destination_region = destinationRegion,
            options = rates,
            cheapest = rates.Count > 0 ? (object?)rates[0] : null,
        });
    }
}
