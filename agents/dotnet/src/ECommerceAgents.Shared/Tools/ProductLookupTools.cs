using Dapper;
using ECommerceAgents.Shared.Data;
using Microsoft.Extensions.AI;
using System.ComponentModel;

namespace ECommerceAgents.Shared.Tools;

/// <summary>
/// Resolves a product name to its id — the .NET twin of Python's
/// <c>shared/tools/product_lookup_tools.py</c>.
///
/// This is the first entry in a shared .NET tool library (issue #18). It is
/// first because without it the specialists that take a <c>productId</c> are
/// unusable from a normal question: asked "what are customers saying about the
/// Dyson V15?", the model has no way to turn that name into an id, so it
/// invents one. Observed live against the real stack before this shipped —
/// review-sentiment called AnalyzeSentiment, GetSentimentTrend and
/// DetectFakeReviews in turn, each with the made-up id "dyson-v15-id", got
/// "Product not found" three times, and told the user the product doesn't
/// exist. It does exist; only the lookup was missing.
///
/// Attached to the same three specialists Python attaches it to
/// (pricing-promotions, review-sentiment, inventory-fulfillment) — the ones
/// whose tools are keyed by product id. product-discovery and order-management
/// don't need it: they already resolve products by name themselves.
/// </summary>
public sealed class ProductLookupTools(DatabasePool pool)
{
    private readonly DatabasePool _pool = pool;

    public IEnumerable<AITool> All() => new AITool[]
    {
        AIFunctionFactory.Create(FindProductByName, nameof(FindProductByName)),
    };

    [Description(
        "Resolve a product's UUID from its name (or a close match). Call this first "
        + "whenever the user refers to a product by name rather than a UUID, before "
        + "calling any tool that requires product_id."
    )]
    public async Task<object> FindProductByName(
        [Description("Product name or a close match, e.g. 'Sony WH-1000XM5'")] string name
    )
    {
        await using var conn = await _pool.OpenAsync();

        // Exact (case-insensitive) match first, then a substring match on every
        // word — the same word-splitting search_products uses, so "Sony
        // headphones" still finds "Sony WH-1000XM5".
        var exact = await conn.QueryFirstOrDefaultAsync<ProductRow>(
            "SELECT id, name FROM products WHERE is_active = TRUE AND name ILIKE @name LIMIT 1",
            new { name }
        );
        if (exact is not null)
        {
            return new { found = true, product_id = exact.Id.ToString(), product_name = exact.Name };
        }

        var words = (name ?? string.Empty)
            .Split(' ', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
            .Where(w => w.Length >= 2)
            .ToList();

        if (words.Count == 0)
        {
            return new { found = false, message = $"No product matching '{name}'" };
        }

        var conditions = string.Join(" AND ", words.Select((_, i) => $"name ILIKE @w{i}"));
        var args = new DynamicParameters();
        for (var i = 0; i < words.Count; i++)
        {
            args.Add($"w{i}", $"%{words[i]}%");
        }

        var row = await conn.QueryFirstOrDefaultAsync<ProductRow>(
            $"SELECT id, name FROM products WHERE is_active = TRUE AND {conditions} LIMIT 1",
            args
        );

        return row is null
            ? new { found = false, message = $"No product matching '{name}'" }
            : new { found = true, product_id = row.Id.ToString(), product_name = row.Name };
    }

    private sealed record ProductRow(Guid Id, string Name);
}
