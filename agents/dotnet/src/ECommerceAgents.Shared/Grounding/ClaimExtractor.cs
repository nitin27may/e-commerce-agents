using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace ECommerceAgents.Shared.Grounding;

public sealed record ProductClaim(string Id, string? Name, decimal? Price);
public sealed record OrderClaim(string Id, string? Status, decimal? Total);

public sealed record ExtractedClaims(
    IReadOnlyList<ProductClaim> Products,
    IReadOnlyList<OrderClaim> Orders,
    IReadOnlyList<string> BareIds,
    IReadOnlyList<decimal> Amounts,
    IReadOnlyList<string> TrackingNumbers
)
{
    public static readonly ExtractedClaims Empty = new([], [], [], [], []);
    public int Total => Products.Count + Orders.Count + BareIds.Count + Amounts.Count + TrackingNumbers.Count;
}

/// <summary>
/// Pulls verifiable claims out of an agent's final text — the .NET twin of
/// Python's <c>shared/grounding/extractor.py</c>.
/// </summary>
/// <remarks>
/// Two tiers, handled differently on purpose:
///
/// <b>Card claims</b> are the fenced ```product / ```products / ```order blocks
/// documented in <c>config/prompts/_shared/grounding-rules.yaml</c>. These are
/// what the UI actually renders as interactive cards, so they're the claims
/// worth checking.
///
/// <b>Prose claims</b> are bare UUIDs, <c>$NNN.NN</c> amounts and TRK tracking
/// numbers mentioned outside any card. They're extracted from the text with
/// the card blocks removed first, so a card's own id and price are never
/// double-counted as separate prose claims.
///
/// Malformed JSON inside a fence is skipped rather than raised: a model that
/// emits a broken card has already failed the UI contract, which is a
/// rendering concern, not a reason to fail grounding.
/// </remarks>
public static partial class ClaimExtractor
{
    [GeneratedRegex(@"```(product|products|order)\s*\n(.*?)\n?```", RegexOptions.Singleline)]
    private static partial Regex CardFence();

    [GeneratedRegex(@"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")]
    private static partial Regex Uuid();

    [GeneratedRegex(@"\$\s?(\d{1,3}(?:,\d{3})+(?:\.\d{2})?|\d{1,6}(?:\.\d{2})?)")]
    private static partial Regex Amount();

    [GeneratedRegex(@"\bTRK[A-Z0-9]+\b")]
    private static partial Regex Tracking();

    public static ExtractedClaims Extract(string? text)
    {
        if (string.IsNullOrWhiteSpace(text))
        {
            return ExtractedClaims.Empty;
        }

        var products = new List<ProductClaim>();
        var orders = new List<OrderClaim>();

        foreach (Match fence in CardFence().Matches(text))
        {
            var kind = fence.Groups[1].Value;
            var body = fence.Groups[2].Value;

            JsonElement parsed;
            try
            {
                parsed = JsonDocument.Parse(body).RootElement;
            }
            catch (JsonException)
            {
                continue;
            }

            // ```products holds an array; ```product and ```order hold one object.
            var items = parsed.ValueKind == JsonValueKind.Array
                ? parsed.EnumerateArray().ToList()
                : [parsed];

            foreach (var item in items)
            {
                if (item.ValueKind != JsonValueKind.Object || !item.TryGetProperty("id", out var idProp))
                {
                    continue;
                }
                // Python reads str(entry["id"]); a numeric id is a claim that will fail its lookup, not a crash.
                var id = idProp.ValueKind == JsonValueKind.String ? idProp.GetString() : idProp.ToString();
                if (string.IsNullOrWhiteSpace(id))
                {
                    continue;
                }

                if (kind == "order")
                {
                    orders.Add(new OrderClaim(id, ReadString(item, "status"), ReadDecimal(item, "total")));
                }
                else
                {
                    products.Add(new ProductClaim(id, ReadString(item, "name"), ReadDecimal(item, "price")));
                }
            }
        }

        // Prose is whatever's left once the cards are removed.
        var prose = CardFence().Replace(text, " ");
        var carded = products.Select(p => p.Id).Concat(orders.Select(o => o.Id)).ToHashSet(StringComparer.OrdinalIgnoreCase);

        var bareIds = Uuid().Matches(prose)
            .Select(m => m.Value)
            .Where(id => !carded.Contains(id))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToList();

        var amounts = Amount().Matches(prose)
            .Select(m => decimal.TryParse(
                m.Groups[1].Value.Replace(",", ""), NumberStyles.Number, CultureInfo.InvariantCulture, out var value)
                ? value
                : (decimal?)null)
            .Where(v => v is not null)
            .Select(v => v!.Value)
            .Distinct()
            .ToList();

        var tracking = Tracking().Matches(prose)
            .Select(m => m.Value)
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToList();

        return new ExtractedClaims(products, orders, bareIds, amounts, tracking);
    }

    /// <summary>
    /// Rewrites the fenced <c>product</c>, <c>products</c> and <c>order</c> blocks in place — the twin of Python's
    /// <c>rewrite_cards</c>, and <c>enforce</c> mode's stripping and correcting mechanism.
    /// </summary>
    /// <remarks>
    /// The deciders receive the parsed entry and return the entry to keep (possibly corrected) or null to drop it.
    /// In a <c>products</c> block entries are filtered one by one and a block left empty is removed. A block with
    /// malformed JSON is left alone, and a block no decider changed is returned byte for byte, so a verified
    /// card's formatting is never disturbed.
    /// </remarks>
    public static string RewriteCards(
        string text,
        Func<JsonObject, JsonObject?> decideProduct,
        Func<JsonObject, JsonObject?> decideOrder)
    {
        return CardFence().Replace(text, match =>
        {
            var kind = match.Groups[1].Value;
            JsonNode? payload;
            try
            {
                payload = JsonNode.Parse(match.Groups[2].Value);
            }
            catch (JsonException)
            {
                return match.Value;
            }

            if (kind == "products")
            {
                if (payload is not JsonArray array)
                {
                    return match.Value;
                }

                var kept = new JsonArray();
                foreach (var entry in array.OfType<JsonObject>())
                {
                    if (decideProduct(entry) is { } decided)
                    {
                        kept.Add(decided.DeepClone());
                    }
                }

                if (JsonNode.DeepEquals(kept, array))
                {
                    return match.Value;
                }

                return kept.Count > 0 ? $"```products\n{kept.ToJsonString()}\n```" : "";
            }

            if (payload is not JsonObject single)
            {
                return match.Value;
            }

            var decide = kind == "order" ? decideOrder : decideProduct;
            var result = decide(single);
            if (result is not null && JsonNode.DeepEquals(result, single))
            {
                return match.Value;
            }

            return result is null ? "" : $"```{kind}\n{result.ToJsonString()}\n```";
        });
    }

    private static string? ReadString(JsonElement e, string name) =>
        e.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;

    private static decimal? ReadDecimal(JsonElement e, string name) =>
        e.TryGetProperty(name, out var v) && v.TryGetDecimal(out var d) ? d : null;
}
