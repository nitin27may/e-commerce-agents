using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace ECommerceAgents.Shared.Grounding;

public sealed record ProductFact(string Id, string? Name, decimal? Price, string? ImageUrl);

public sealed record OrderFact(string Id, string? Status, decimal? Total, string? Tracking);

public sealed record PromoFact(string Code, decimal? DiscountAmount, string? DiscountType);

/// <summary>
/// Typed facts recorded from this request's tool results — the .NET twin of Python's <c>GroundingLedger</c>
/// (<c>shared/grounding/ledger.py</c>). The key list and the recognition rules are the same on both stacks and
/// are pinned by one shared vector file, <c>agents/python/config/grounding/testdata/ledger.v1.json</c>.
/// </summary>
/// <remarks>
/// <para>
/// Tool results are not uniformly shaped: product tools key their id as <c>id</c>, <c>get_order_details</c> as
/// <c>order_id</c>, and the stock and loyalty tools return neither. Facts are recognised by duck-typing on the
/// keys a tool shape actually returns. A .NET tool returns a record or an anonymous object, so the result is
/// first turned into a JSON tree and every property name is snake_cased: <c>OrderId</c>, <c>orderId</c> and
/// <c>order_id</c> are one key, which is what lets one recogniser serve the C# records and the Python dicts.
/// </para>
/// <para>
/// One ledger is shared by reference for a whole request, and a function-invocation loop may run tool calls
/// concurrently, so every mutation takes the lock. Recording never throws: a result it cannot read is skipped, a
/// tool call is never failed by the grounding layer.
/// </para>
/// </remarks>
public sealed partial class GroundingLedger
{
    private readonly object _gate = new();

    public Dictionary<string, ProductFact> Products { get; } = new(StringComparer.OrdinalIgnoreCase);
    public Dictionary<string, OrderFact> Orders { get; } = new(StringComparer.OrdinalIgnoreCase);
    public Dictionary<string, PromoFact> Promos { get; } = new(StringComparer.Ordinal);

    /// <summary>
    /// Amounts that fit none of the product, order or promo shapes: a price history's aggregates, a loyalty
    /// tier's thresholds, a cart's totals. The keys are listed in <see cref="AmountKeys"/>.
    /// </summary>
    public HashSet<decimal> KnownAmounts { get; } = [];

    /// <summary>
    /// Values the caller put in: amounts and ids typed in this turn's user message or passed as a tool argument.
    /// An answer that echoes them is quoting the user, so the verifier reports them as <c>user_supplied</c>. They
    /// never make a model-made figure verified: only a value that literally appears in the user's text or the
    /// call arguments lands here.
    /// </summary>
    public HashSet<decimal> UserAmounts { get; } = [];

    public HashSet<string> UserIds { get; } = new(StringComparer.OrdinalIgnoreCase);

    /// <summary>
    /// The amount-bearing keys, identical to Python's <c>_record_bare_amount</c> list. A new tool field a model
    /// quotes belongs here and in the shared vector file, in the same change on both stacks.
    /// </summary>
    public static readonly IReadOnlyList<string> AmountKeys =
    [
        "price", "current_price", "average_price", "min_price", "max_price", "amount", "total", "discount_amount",
        "original_price",
        "spend_needed", "total_spend", "min_spend", "free_shipping_threshold", "max_discount",
        "min_spend_required", "bundle_total", "savings", "original_total", "total_savings", "final_total",
        "shipping_cost", "total_shipping_cost",
    ];

    public void NoteUserText(object? text)
    {
        var (amounts, ids) = UserSupplied.Values(text);
        lock (_gate)
        {
            UserAmounts.UnionWith(amounts);
            UserIds.UnionWith(ids);
        }
    }

    public void NoteToolArguments(object? arguments) => NoteUserText(arguments);

    /// <summary>Records every recognisable fact in a tool's return value (a record, a list, JSON, or a JSON string).</summary>
    public void Record(object? result)
    {
        JsonNode? tree;
        try
        {
            tree = Normalize(result);
        }
        catch (Exception ex) when (ex is JsonException or NotSupportedException or InvalidOperationException)
        {
            return;
        }

        if (tree is null)
        {
            return;
        }

        lock (_gate)
        {
            foreach (var item in IterObjects(tree))
            {
                RecordOne(item);
                foreach (var value in item.Select(p => p.Value))
                {
                    if (value is JsonArray array)
                    {
                        foreach (var nested in array.OfType<JsonObject>())
                        {
                            RecordOne(nested);
                            RecordBareAmount(nested);
                        }
                    }
                    else if (value is JsonObject obj)
                    {
                        // get_loyalty_tier nests its next-tier amounts one object down.
                        RecordBareAmount(obj);
                    }
                }

                RecordBareAmount(item);
            }
        }
    }

    private void RecordBareAmount(JsonObject item)
    {
        foreach (var key in AmountKeys)
        {
            if (AsDecimal(item[key]) is { } value)
            {
                KnownAmounts.Add(value);
            }
        }

        // get_active_deals lists coupons as {discount_type, discount_value}; only a fixed one is dollars
        // ("$10 off"): a percentage's 15.0 must not make "$15" read as grounded.
        if (AsString(item["discount_type"]) == "fixed" && AsDecimal(item["discount_value"]) is { } fixedValue)
        {
            KnownAmounts.Add(fixedValue);
        }
    }

    private void RecordOne(JsonObject item)
    {
        if (LooksLikeProduct(item))
        {
            if (AsString(item["id"], coerce: true) is { Length: > 0 } id)
            {
                Products[id] = new ProductFact(
                    id, AsString(item["name"]), AsDecimal(item["price"]), AsString(item["image_url"]));
            }
        }
        else if (LooksLikeOrder(item))
        {
            var orderId = FirstNonEmpty(AsString(item["order_id"], coerce: true), AsString(item["id"], coerce: true));
            if (orderId is null)
            {
                return;
            }

            Orders[orderId] = new OrderFact(
                orderId,
                AsString(item["status"]),
                AsDecimal(item["total"]),
                FirstNonEmpty(AsString(item["tracking_number"]), AsString(item["tracking"])));
        }
        else if (LooksLikePromo(item))
        {
            var code = (AsString(item["code"], coerce: true) ?? "").ToUpperInvariant();
            if (code.Length > 0)
            {
                Promos[code] = new PromoFact(code, AsDecimal(item["discount_amount"]), AsString(item["discount_type"]));
            }
        }
    }

    // search_products / get_product_details / compare_products / semantic_search / find_similar_products /
    // get_trending_products all return id + price + name at the top level; order line items use unit_price, not
    // price, so they never collide with this shape.
    private static bool LooksLikeProduct(JsonObject item) =>
        item.ContainsKey("id") && item.ContainsKey("price") && item.ContainsKey("name") && !item.ContainsKey("order_id");

    // get_order_details returns order_id + status + total + items.
    private static bool LooksLikeOrder(JsonObject item) =>
        item.ContainsKey("status") && item.ContainsKey("total")
        && (item.ContainsKey("order_id") || item.ContainsKey("items"));

    // validate_coupon's success shape: valid=true, code, discount_amount, ...
    private static bool LooksLikePromo(JsonObject item) =>
        item.ContainsKey("code") && item.ContainsKey("discount_amount")
        && item["valid"] is JsonValue flag && flag.GetValueKind() == JsonValueKind.True;

    private static IEnumerable<JsonObject> IterObjects(JsonNode tree) => tree switch
    {
        JsonObject obj => [obj],
        JsonArray array => array.OfType<JsonObject>(),
        _ => [],
    };

    /// <summary>
    /// A tool result as a JSON tree with snake_case property names. A string is parsed when it holds a JSON
    /// object or array, and ignored otherwise.
    /// </summary>
    internal static JsonNode? Normalize(object? result)
    {
        JsonNode? node = result switch
        {
            null => null,
            JsonNode n => n.DeepClone(),
            JsonElement e => JsonNode.Parse(e.GetRawText()),
            string s => ParseIfJson(s),
            _ => JsonSerializer.SerializeToNode(result, result.GetType()),
        };
        return node is null ? null : SnakeCaseKeys(node);
    }

    private static JsonNode? ParseIfJson(string text)
    {
        var trimmed = text.TrimStart();
        return trimmed.StartsWith('{') || trimmed.StartsWith('[') ? JsonNode.Parse(text) : null;
    }

    private static JsonNode SnakeCaseKeys(JsonNode node)
    {
        switch (node)
        {
            case JsonObject obj:
                var renamed = new JsonObject();
                foreach (var (key, value) in obj.ToList())
                {
                    renamed[ToSnakeCase(key)] = value is null ? null : SnakeCaseKeys(value.DeepClone());
                }
                return renamed;
            case JsonArray array:
                var copy = new JsonArray();
                foreach (var value in array)
                {
                    copy.Add(value is null ? null : SnakeCaseKeys(value.DeepClone()));
                }
                return copy;
            default:
                return node;
        }
    }

    internal static string ToSnakeCase(string name) =>
        CamelBoundary().Replace(name, "$1_$2").ToLowerInvariant();

    [GeneratedRegex(@"([a-z0-9])([A-Z])")]
    private static partial Regex CamelBoundary();

    private static string? AsString(JsonNode? node, bool coerce = false)
    {
        if (node is not JsonValue value)
        {
            return null;
        }

        return value.GetValueKind() switch
        {
            JsonValueKind.String => value.GetValue<string>(),
            JsonValueKind.Number when coerce => value.ToJsonString(),
            _ => null,
        };
    }

    private static string? FirstNonEmpty(params string?[] values) =>
        values.FirstOrDefault(v => !string.IsNullOrEmpty(v));

    private static decimal? AsDecimal(JsonNode? node)
    {
        if (node is not JsonValue value)
        {
            return null;
        }

        switch (value.GetValueKind())
        {
            case JsonValueKind.Number:
                return decimal.TryParse(
                    value.ToJsonString(), NumberStyles.Float, CultureInfo.InvariantCulture, out var number)
                    ? number
                    : null;
            case JsonValueKind.String:
                // Python's float("12.5") reads a numeric string too.
                return decimal.TryParse(
                    value.GetValue<string>(), NumberStyles.Float, CultureInfo.InvariantCulture, out var parsed)
                    ? parsed
                    : null;
            default:
                return null;
        }
    }
}
