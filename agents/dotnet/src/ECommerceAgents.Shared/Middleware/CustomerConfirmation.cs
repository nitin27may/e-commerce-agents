using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace ECommerceAgents.Shared.Middleware;

/// <summary>
/// The pure half of the customer's own confirmation of a gated action: what counts as yes, which arguments make two requests the
/// same action, and the sentences the customer reads. The .NET twin of Python's <c>shared/customer_confirmation.py</c>; both stacks
/// are pinned to <c>agents/python/config/policies/testdata/customer-confirmation.v1.json</c> (#302, #307).
/// </summary>
/// <remarks>
/// Nothing here asks a model whether the customer agreed. The record (<see cref="Approvals.CustomerConfirmationRepository"/>) is
/// one open question per (user, session, tool, normalised arguments); a yes can only answer a question asked on an earlier turn;
/// the user's words come from the stored conversation, never from text the orchestrator's model composed for the specialist.
/// This is not an approval: no inbox row, no audit event. Staff approval still follows, unchanged.
/// </remarks>
public static partial class CustomerConfirmation
{
    public const string NotSubmitted = "I have not submitted anything for approval.";
    public const string DeclinedReply = "Understood. " + NotSubmitted;

    // ─────────────────────── What counts as yes ───────────────────────

    private const string Core =
        "(?:yes|yep|yeah|yup|sure|ok|okay|confirm|confirmed|i confirm|go ahead|proceed|do it|please do|absolutely|"
        + "definitely|affirmative|correct|that is correct|thats correct|that is right|thats right|sounds good)";

    private const string Filler =
        "(?:please|thanks|thank you|now|and|it|that|this|that is right|thats right|that is correct|thats correct|"
        + "go ahead|do it|proceed|confirm|confirmed|cancel it|cancel the order|submit it|submit|send it|"
        + "go for it|yes|sure|ok|okay|i do|i am sure|im sure|"
        + "that is fine|thats fine|fine|lets do it|let us do it|of course|for sure)";

    private static readonly Regex Affirmative = new(
        $"^(?:please )?{Core}(?: {Filler}){{0,4}}$", RegexOptions.CultureInvariant | RegexOptions.Compiled);

    private static readonly HashSet<string> Negations = new(StringComparer.Ordinal)
    {
        "no", "nope", "nah", "not", "dont", "never", "nevermind", "stop", "wait", "hold", "but", "however", "except", "instead",
    };

    private static readonly HashSet<string> DeclineTokens = new(StringComparer.Ordinal)
    {
        "no", "nope", "nah", "not", "dont", "never", "nevermind", "stop", "wait", "forget",
    };

    private const int MaxAffirmativeTokens = 8;

    [GeneratedRegex("[^a-z0-9]+", RegexOptions.CultureInvariant)]
    private static partial Regex NonWord();

    private static readonly char[] ZeroWidth = ['​', '‌', '‍', '⁠', '﻿', '­'];

    private static char FoldWidth(char c) => c switch
    {
        >= '\uFF01' and <= '\uFF5E' => (char)(c - 0xFEE0),
        '\u3000' => ' ',
        _ => c,
    };

    /// <summary>Lower-cased words only: compatibility-folded, zero-width characters and punctuation gone, spaces collapsed. <c>don't</c> and <c>dont</c> are the same word.</summary>
    public static string NormalizeText(string? text)
    {
        if (string.IsNullOrEmpty(text))
        {
            return "";
        }

        // The services run with InvariantGlobalization, where string.Normalize does not fold compatibility characters, so the
        // fullwidth forms (the ones a keyboard layout can actually produce) are folded by hand after it. Any other compatibility
        // character stays as it is, which fails closed: it is not a letter of any yes word, so it never confirms.
        var folded = text.Normalize(NormalizationForm.FormKC);
        folded = string.Concat(folded.Where(c => Array.IndexOf(ZeroWidth, c) < 0).Select(FoldWidth)).ToLowerInvariant();
        folded = folded.Replace("'", "", StringComparison.Ordinal).Replace("’", "", StringComparison.Ordinal);
        return string.Join(' ', NonWord().Replace(folded, " ").Split(' ', StringSplitOptions.RemoveEmptyEntries));
    }

    /// <summary>True only for a short, explicit yes. The whole message must be the agreement: no question, no negation, nothing asked for beyond it.</summary>
    public static bool IsAffirmative(string? message)
    {
        if (string.IsNullOrEmpty(message) || message.Contains('?', StringComparison.Ordinal))
        {
            return false;
        }

        var normalized = NormalizeText(message);
        var tokens = normalized.Split(' ', StringSplitOptions.RemoveEmptyEntries);
        if (tokens.Length == 0 || tokens.Length > MaxAffirmativeTokens || tokens.Any(Negations.Contains))
        {
            return false;
        }

        return Affirmative.IsMatch(normalized);
    }

    /// <summary>A clear no (or a stop). Anything else that is not a yes is silence about the question, not a refusal.</summary>
    public static bool IsDecline(string? message)
    {
        if (string.IsNullOrEmpty(message) || IsAffirmative(message))
        {
            return false;
        }

        return NormalizeText(message).Split(' ', StringSplitOptions.RemoveEmptyEntries).Any(DeclineTokens.Contains);
    }

    // ─────────────────────── The key ───────────────────────

    /// <summary>tool -> the arguments that decide what the action does. <c>reason</c> is deliberately absent: the model words it differently every turn.</summary>
    private static readonly Dictionary<string, string[]> KeyFields = new(StringComparer.Ordinal)
    {
        ["cancel_order"] = ["order_id"],
        ["modify_order"] = ["order_id", "new_address"],
        ["initiate_return"] = ["order_id", "refund_method"],
        ["process_refund"] = ["return_id"],
        ["place_backorder"] = ["product_id", "quantity"],
    };

    private static readonly string[] AddressOrder = ["street", "address", "line1", "line2", "city", "state", "zip", "postal_code", "country"];

    /// <summary>The gated tools this confirmation knows how to key and describe (every tool <see cref="HitlGate"/> gates).</summary>
    public static IReadOnlyCollection<string> KnownTools => KeyFields.Keys;

    private static bool IsEmpty(JsonElement value) =>
        value.ValueKind is JsonValueKind.Null or JsonValueKind.Undefined
        || (value.ValueKind == JsonValueKind.String && value.GetString()!.Length == 0);

    private static string CollapseSpaces(string text) =>
        string.Join(' ', text.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries)).ToLowerInvariant();

    private static object NormValue(JsonElement value)
    {
        switch (value.ValueKind)
        {
            case JsonValueKind.Object:
                var map = new SortedDictionary<string, object>(StringComparer.Ordinal);
                foreach (var property in value.EnumerateObject().OrderBy(p => p.Name, StringComparer.Ordinal))
                {
                    if (!IsEmpty(property.Value))
                    {
                        map[property.Name.Trim().ToLowerInvariant()] = NormValue(property.Value);
                    }
                }

                return map;
            case JsonValueKind.True:
                return true;
            case JsonValueKind.False:
                return false;
            case JsonValueKind.Number:
                var number = value.GetDouble();
                return number == Math.Floor(number) && Math.Abs(number) < 9e15 ? (object)(long)number : number;
            default:
                return CollapseSpaces(value.ValueKind == JsonValueKind.String ? value.GetString()! : value.GetRawText());
        }
    }

    /// <summary>The arguments that identify the action, normalised so a re-worded or re-cased repeat compares equal.</summary>
    public static SortedDictionary<string, object> NormalizedArguments(string toolName, JsonElement input)
    {
        var source = new Dictionary<string, JsonElement>(StringComparer.Ordinal);
        if (input.ValueKind == JsonValueKind.Object)
        {
            foreach (var property in input.EnumerateObject())
            {
                source[property.Name] = property.Value;
            }
        }

        IEnumerable<string> names = KeyFields.TryGetValue(toolName, out var fields) ? fields : source.Keys;
        var result = new SortedDictionary<string, object>(StringComparer.Ordinal);
        foreach (var name in names)
        {
            if (source.TryGetValue(name, out var found) && !IsEmpty(found))
            {
                result[name] = NormValue(found);
            }
            else if (toolName == "initiate_return" && name == "refund_method")
            {
                // The tool's own default, so an omitted method and an explicit "original_payment" are one action.
                result[name] = "original_payment";
            }
        }

        return result;
    }

    /// <summary>Compact JSON with sorted keys and every non-ASCII character escaped: Python's <c>json.dumps(sort_keys=True, separators=(",", ":"))</c>.</summary>
    public static string Canonical(object? value)
    {
        var sb = new StringBuilder();
        Write(sb, value);
        return sb.ToString();
    }

    private static void Write(StringBuilder sb, object? value)
    {
        switch (value)
        {
            case SortedDictionary<string, object> map:
                sb.Append('{');
                var first = true;
                foreach (var (key, item) in map)
                {
                    if (!first)
                    {
                        sb.Append(',');
                    }

                    first = false;
                    WriteString(sb, key);
                    sb.Append(':');
                    Write(sb, item);
                }

                sb.Append('}');
                break;
            case bool b:
                sb.Append(b ? "true" : "false");
                break;
            case long l:
                sb.Append(l.ToString(CultureInfo.InvariantCulture));
                break;
            case double d:
                sb.Append(d.ToString("R", CultureInfo.InvariantCulture));
                break;
            case string s:
                WriteString(sb, s);
                break;
            default:
                sb.Append("null");
                break;
        }
    }

    private static void WriteString(StringBuilder sb, string text)
    {
        sb.Append('"');
        foreach (var c in text)
        {
            switch (c)
            {
                case '"': sb.Append("\\\""); break;
                case '\\': sb.Append("\\\\"); break;
                case '\n': sb.Append("\\n"); break;
                case '\r': sb.Append("\\r"); break;
                case '\t': sb.Append("\\t"); break;
                case '\b': sb.Append("\\b"); break;
                case '\f': sb.Append("\\f"); break;
                case >= ' ' and <= '~': sb.Append(c); break;
                default: sb.Append("\\u").Append(((int)c).ToString("x4", CultureInfo.InvariantCulture)); break;
            }
        }

        sb.Append('"');
    }

    /// <summary>sha256 (hex) of the tool, a newline and the canonical JSON of the normalised identifying arguments.</summary>
    public static string ArgsKey(string toolName, JsonElement input)
    {
        var canonical = Canonical(NormalizedArguments(toolName, input));
        return Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes($"{toolName}\n{canonical}"))).ToLowerInvariant();
    }

    // ─────────────────────── The sentence ───────────────────────

    /// <summary>Python's <c>str(value).strip()</c> for a JSON value; an absent or null one is "".</summary>
    private static string Str(JsonElement value) => value.ValueKind switch
    {
        JsonValueKind.Null or JsonValueKind.Undefined => "",
        JsonValueKind.String => value.GetString()!.Trim(),
        JsonValueKind.True => "True",
        JsonValueKind.False => "False",
        _ => value.GetRawText().Trim(),
    };

    private static string Text(JsonElement input, string name) =>
        input.ValueKind == JsonValueKind.Object && input.TryGetProperty(name, out var value) ? Str(value) : "";

    private static string AddressText(JsonElement input)
    {
        if (input.ValueKind != JsonValueKind.Object || !input.TryGetProperty("new_address", out var value))
        {
            return "";
        }

        if (value.ValueKind != JsonValueKind.Object)
        {
            return Str(value);
        }

        var names = AddressOrder.Where(k => value.TryGetProperty(k, out _)).ToList();
        names.AddRange(value.EnumerateObject().Select(p => p.Name).Where(n => Array.IndexOf(AddressOrder, n) < 0));
        var parts = names
            .Select(n => value.GetProperty(n).ValueKind == JsonValueKind.Null ? "None" : Str(value.GetProperty(n)))
            .Where(part => part.Length > 0);
        return string.Join(", ", parts);
    }

    /// <summary>The action in plain words, with the arguments the customer is agreeing to.</summary>
    public static string DescribeAction(string toolName, JsonElement input)
    {
        var order = Text(input, "order_id");
        switch (toolName)
        {
            case "cancel_order":
                return order.Length > 0 ? $"cancel order {order}" : "cancel an order";
            case "modify_order":
                var change = order.Length > 0 ? $"change the shipping address on order {order}" : "change a shipping address";
                var address = AddressText(input);
                return address.Length > 0 ? $"{change} to {address}" : change;
            case "initiate_return":
                var start = order.Length > 0 ? $"start a return for order {order}" : "start a return";
                var method = Text(input, "refund_method").Replace('_', ' ');
                return method.Length > 0 ? $"{start}, refunding to {method}" : start;
            case "process_refund":
                var returnId = Text(input, "return_id");
                return returnId.Length > 0 ? $"process the refund for return {returnId}" : "process a refund";
            case "place_backorder":
                var product = Text(input, "product_id");
                var quantity = Text(input, "quantity");
                var backorder = quantity.Length > 0 ? $"place a backorder for {quantity} units" : "place a backorder";
                return product.Length > 0 ? $"{backorder} of product {product}" : backorder;
            default:
                return toolName.Replace('_', ' ');
        }
    }

    /// <summary>What the customer reads when the model asks for a gated call. Deterministic: no model produces it.</summary>
    public static string Prompt(string toolName, JsonElement input) =>
        $"Before I send this for approval, please confirm: you want me to {DescribeAction(toolName, input)}. "
        + $"Reply \"yes\" to confirm, or tell me what to change. {NotSubmitted}";
}
