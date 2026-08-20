using System.Collections;
using System.Reflection;
using System.Text.Json;

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
            case JsonElement:
                // Specs/free-form JSON payloads are left opaque — JsonElement is a readonly
                // struct with no in-place mutation path, and the top-level Name/Description
                // fields already covered by the tool's own allowlist are the primary vector.
                return value;
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
                dict[k] = SanitizeValue(dict[k], fields, k as string);
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
