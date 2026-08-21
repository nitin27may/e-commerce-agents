using Dapper;
using ECommerceAgents.Shared.Data;
using Microsoft.Extensions.AI;
using System.ComponentModel;

namespace ECommerceAgents.Shared.Tools;

/// <summary>
/// Price trend over a window — the .NET twin of Python's
/// <c>shared/tools/pricing_tools.py</c> (#18).
/// </summary>
/// <remarks>
/// The query already existed in .NET as
/// <c>IPrePurchaseTools.GetPriceHistoryAsync</c>, but only as a workflow
/// interface method — never registered as an <c>AIFunction</c>, so no agent
/// could call it. Attached here to the same two agents Python attaches it to:
/// product-discovery and pricing-promotions.
/// </remarks>
public sealed class PriceHistoryTools(DatabasePool pool)
{
    private readonly DatabasePool _pool = pool;

    public IEnumerable<AITool> All() => new AITool[]
    {
        AIFunctionFactory.Create(GetPriceHistory, nameof(GetPriceHistory)),
    };

    [Description(
        "Get price history for a product over a specified number of days. "
        + "Useful for showing price trends and identifying deals."
    )]
    public async Task<object> GetPriceHistory(
        [Description("UUID of the product")] string productId,
        [Description("Number of days of history (30, 60, or 90)")] int days = 30
    )
    {
        if (!Guid.TryParse(productId, out var id))
        {
            return new { error = $"Product not found: {productId}" };
        }

        await using var conn = await _pool.OpenAsync();

        var product = await conn.QueryFirstOrDefaultAsync(
            "SELECT name, price FROM products WHERE id = @id", new { id }
        );
        if (product is null)
        {
            return new { error = $"Product not found: {productId}" };
        }

        var productName = (string)product.name;
        var current = (decimal)product.price;

        var prices = (await conn.QueryAsync<decimal>(
            @"SELECT price FROM price_history
              WHERE product_id = @id AND recorded_at >= NOW() - (@days || ' days')::interval
              ORDER BY recorded_at",
            new { id, days = days.ToString() }
        )).ToList();

        if (prices.Count == 0)
        {
            return new
            {
                product_id = productId,
                product_name = productName,
                current_price = current,
                history = Array.Empty<object>(),
                summary = "No price history available",
            };
        }

        var average = prices.Average();

        // Needs a fortnight of points to compare a leading and trailing week;
        // below that the label would be noise dressed as a trend. Matches
        // Python's own threshold and its 5% deadband.
        var trend = prices.Count >= 7
            ? prices.TakeLast(7).Average() < prices.Take(7).Average() * 0.95m ? "decreasing"
                : prices.TakeLast(7).Average() > prices.Take(7).Average() * 1.05m ? "increasing"
                : "stable"
            : "insufficient_data";

        return new
        {
            product_id = productId,
            product_name = productName,
            current_price = current,
            period_days = days,
            average_price = Math.Round(average, 2),
            min_price = Math.Round(prices.Min(), 2),
            max_price = Math.Round(prices.Max(), 2),
            trend,
            is_good_deal = current <= average * 0.95m,
            data_points = prices.Count,
        };
    }
}
