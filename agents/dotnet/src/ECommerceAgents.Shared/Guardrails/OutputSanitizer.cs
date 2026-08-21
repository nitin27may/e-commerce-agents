using System.Collections;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace ECommerceAgents.Shared.Guardrails;

/// <summary>
/// Recursively neutralizes untrusted strings inside a tool result — the
/// .NET twin of Python's <c>neutralize_value</c> (<c>sanitize.py</c>).
/// </summary>
/// <remarks>
/// Python tools return plain dicts, so <c>neutralize_value</c> walks dict
/// keys directly. .NET specialist tools return strongly-typed <c>record</c>
/// types instead, so this walks public instance properties via reflection —
/// a <c>record</c>'s positional (<c>init</c>-only) properties are still
/// writable through <see cref="PropertyInfo.SetValue"/>, which bypasses the
/// C# compiler's <c>init</c> restriction (a source-level rule, not a
/// runtime one), so values are mutated in place exactly like the Python
/// dict-mutation approach rather than requiring a rebuilt copy.
/// </remarks>
public static class OutputSanitizer
{
    /// <param name="value">A tool's return value — record, list, dictionary, or scalar.</param>
    /// <param name="fields">If given, only string-typed properties/dict-keys/list-items whose
    /// name (or the name of the property holding the list) is in this set are neutralized;
    /// pass <c>null</c> to neutralize every string reached.</param>
    public static object? Sanitize(object? value, HashSet<string>? fields) => SanitizeValue(value, fields, key: null);

    private static object? SanitizeValue(object? value, HashSet<string>? fields, string? key)
    {
        switch (value)
        {
            case null:
                return null;
            case string s:
                return (fields is null || (key is not null && fields.Contains(key)))
                    ? Guardrails.Sanitize.NeutralizeText(s)
                    : s;
            case JsonElement json:
                // JSON payloads must be walked, not skipped. products.specs is JSONB and
                // seller-editable, so leaving it opaque made it an unsanitized route for
                // injection text to reach the model — one Python's neutralize_value closes,
                // since it recurses through dicts and lists without exception. JsonElement
                // being an immutable struct is not a reason to skip it, only a reason to
                // rebuild rather than mutate in place. See issue #31.
                return SanitizeJson(json, fields, key);
        }

        var type = value.GetType();
        if (IsOpaqueScalar(type))
        {
            return value;
        }

        if (value is IDictionary dict)
        {
            foreach (var k in dict.Keys.Cast<object>().ToList())
            {
                // Scope is sticky: once a key matches the allowlist, everything
                // beneath it stays in scope. Python matches on the *immediate*
                // key only (shared/guardrails/sanitize.py::neutralize_value), which
                // means an entry like "specs" there only covers specs-as-a-string,
                // not the nested dict products.specs actually is. Deliberate
                // divergence, in the safer direction — a container listed as
                // untrusted should have an untrusted interior.
                var name = k as string;
                var scope = fields is not null && name is not null && fields.Contains(name) ? name : key;
                dict[k] = SanitizeValue(dict[k], fields, scope);
            }
            return value;
        }

        if (value is IList list)
        {
            for (var i = 0; i < list.Count; i++)
            {
                list[i] = SanitizeValue(list[i], fields, key);
            }
            return value;
        }

        // A record/class instance: walk its public, writable instance properties.
        foreach (var prop in type.GetProperties(BindingFlags.Public | BindingFlags.Instance))
        {
            if (!prop.CanRead || prop.GetIndexParameters().Length > 0)
            {
                continue;
            }

            var propValue = prop.GetValue(value);
            var sanitized = SanitizeValue(propValue, fields, prop.Name);
            if (!ReferenceEquals(sanitized, propValue) && prop.CanWrite)
            {
                prop.SetValue(value, sanitized);
            }
        }

        return value;
    }

    /// <summary>
    /// Rebuilds a <see cref="JsonElement"/> with every in-scope string neutralized.
    /// Returns the original instance when nothing changed, so the caller's
    /// <c>ReferenceEquals</c> write-back check stays meaningful.
    /// </summary>
    private static object SanitizeJson(JsonElement json, HashSet<string>? fields, string? key)
    {
        var node = SanitizeJsonNode(JsonNode.Parse(json.GetRawText()), fields, key, out var changed);
        if (!changed)
        {
            return json;
        }

        // Round-trip back to JsonElement so the value's runtime type is unchanged
        // from the tool's perspective — callers serialize these straight into the
        // model's context and should not have to care that we rewrote one.
        return JsonSerializer.Deserialize<JsonElement>(node?.ToJsonString() ?? "null");
    }

    private static JsonNode? SanitizeJsonNode(JsonNode? node, HashSet<string>? fields, string? key, out bool changed)
    {
        changed = false;
        switch (node)
        {
            case JsonObject obj:
            {
                foreach (var name in obj.Select(kv => kv.Key).ToList())
                {
                    // A nested object's own property names take over as the scope key,
                    // so an allowlist entry like "specs" covers the whole subtree while
                    // a specific entry like "description" still matches by name.
                    var scope = fields is not null && fields.Contains(name) ? name : key;
                    var replacement = SanitizeJsonNode(obj[name], fields, scope, out var childChanged);
                    if (childChanged)
                    {
                        obj[name] = replacement;
                        changed = true;
                    }
                }
                return obj;
            }
            case JsonArray arr:
            {
                for (var i = 0; i < arr.Count; i++)
                {
                    var replacement = SanitizeJsonNode(arr[i], fields, key, out var childChanged);
                    if (childChanged)
                    {
                        arr[i] = replacement;
                        changed = true;
                    }
                }
                return arr;
            }
            case JsonValue value when value.TryGetValue<string>(out var text):
            {
                if (fields is not null && (key is null || !fields.Contains(key)))
                {
                    return node;
                }
                var cleaned = Guardrails.Sanitize.NeutralizeText(text);
                if (cleaned == text)
                {
                    return node;
                }
                changed = true;
                return JsonValue.Create(cleaned);
            }
            default:
                return node;
        }
    }

    private static bool IsOpaqueScalar(Type type)
    {
        var t = Nullable.GetUnderlyingType(type) ?? type;
        return t.IsPrimitive
            || t.IsEnum
            || t == typeof(decimal)
            || t == typeof(DateTime)
            || t == typeof(DateTimeOffset)
            || t == typeof(Guid)
            || t == typeof(TimeSpan);
    }
}
