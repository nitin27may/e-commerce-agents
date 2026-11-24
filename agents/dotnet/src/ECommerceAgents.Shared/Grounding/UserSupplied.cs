using System.Collections;
using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace ECommerceAgents.Shared.Grounding;

/// <summary>
/// Amounts and UUIDs a caller supplied, from the user's message or a tool call's arguments — the .NET twin of
/// Python's <c>user_supplied_values</c> (<c>shared/grounding/verifier.py</c>).
/// </summary>
/// <remarks>
/// In free text only a figure that reads as money counts: a "$" prefix, a decimal point, or a currency word. A
/// bare "2" (a quantity) is not an amount the user supplied. A number inside structured arguments is an amount
/// as it stands. UUIDs are removed from text before numbers are read, so a digit run inside an id is never an
/// amount.
/// </remarks>
public static partial class UserSupplied
{
    private const string MoneyNum = @"\d{1,6}(?:,\d{3})*(?:\.\d+)?";

    [GeneratedRegex(
        @"\$\s?(" + MoneyNum + @")"
            + @"|(?<![\w.$])(\d{1,6}(?:,\d{3})*\.\d+)(?!\w)"
            + @"|(?<![\w.$])(" + MoneyNum + @")(?=\s*(?:dollars|usd|bucks)\b)",
        RegexOptions.IgnoreCase)]
    private static partial Regex NumberPattern();

    [GeneratedRegex(@"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")]
    private static partial Regex UuidPattern();

    public static (HashSet<decimal> Amounts, HashSet<string> Ids) Values(object? source)
    {
        var amounts = new HashSet<decimal>();
        var ids = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        Walk(source, amounts, ids, depth: 0);
        return (amounts, ids);
    }

    // Convert.ToDecimal(double) is exact on .NET 11 (14.99 becomes 14.990000000000000213...), where earlier
    // runtimes rounded to 15 significant digits. A user-typed amount is what the shortest round-trip text says.
    private static bool TryShortestDecimal(object node, out decimal value)
    {
        value = 0m;
        var text = node switch
        {
            float f when float.IsFinite(f) => f.ToString("R", CultureInfo.InvariantCulture),
            double d when double.IsFinite(d) => d.ToString("R", CultureInfo.InvariantCulture),
            _ => null,
        };
        return text is not null
            && decimal.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out value);
    }

    private static void Walk(object? node, HashSet<decimal> amounts, HashSet<string> ids, int depth)
    {
        if (node is null or bool || depth > 16)
        {
            return;
        }

        switch (node)
        {
            case string text:
                FromText(text, amounts, ids);
                return;
            case JsonElement element:
                FromJson(element, amounts, ids, depth);
                return;
            case JsonNode jsonNode:
                FromJson(JsonSerializer.SerializeToElement(jsonNode), amounts, ids, depth);
                return;
            case decimal d:
                amounts.Add(d);
                return;
            case double or float:
                if (TryShortestDecimal(node, out var shortest))
                {
                    amounts.Add(shortest);
                }
                return;
            case sbyte or byte or short or ushort or int or uint or long or ulong:
                amounts.Add(Convert.ToDecimal(node, CultureInfo.InvariantCulture));
                return;
            case IDictionary dictionary:
                foreach (var value in dictionary.Values)
                {
                    Walk(value, amounts, ids, depth + 1);
                }
                return;
            case IEnumerable<KeyValuePair<string, object?>> pairs:
                foreach (var pair in pairs)
                {
                    Walk(pair.Value, amounts, ids, depth + 1);
                }
                return;
            case IEnumerable items:
                foreach (var item in items)
                {
                    Walk(item, amounts, ids, depth + 1);
                }
                return;
        }
    }

    private static void FromJson(JsonElement element, HashSet<decimal> amounts, HashSet<string> ids, int depth)
    {
        switch (element.ValueKind)
        {
            case JsonValueKind.Number:
                if (element.TryGetDecimal(out var number))
                {
                    amounts.Add(number);
                }
                break;
            case JsonValueKind.String:
                FromText(element.GetString() ?? "", amounts, ids);
                break;
            case JsonValueKind.Object:
                foreach (var property in element.EnumerateObject())
                {
                    Walk(property.Value, amounts, ids, depth + 1);
                }
                break;
            case JsonValueKind.Array:
                foreach (var item in element.EnumerateArray())
                {
                    Walk(item, amounts, ids, depth + 1);
                }
                break;
        }
    }

    private static void FromText(string text, HashSet<decimal> amounts, HashSet<string> ids)
    {
        foreach (Match uuid in UuidPattern().Matches(text))
        {
            ids.Add(uuid.Value.ToLowerInvariant());
        }

        var stripped = UuidPattern().Replace(text, " ");
        foreach (Match match in NumberPattern().Matches(stripped))
        {
            var raw = match.Groups.Cast<Group>().Skip(1).FirstOrDefault(g => g.Success && g.Length > 0)?.Value;
            if (raw is not null
                && decimal.TryParse(raw.Replace(",", ""), NumberStyles.Number, CultureInfo.InvariantCulture, out var value))
            {
                amounts.Add(value);
            }
        }
    }
}
