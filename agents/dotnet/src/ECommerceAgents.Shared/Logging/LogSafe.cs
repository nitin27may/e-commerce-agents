namespace ECommerceAgents.Shared.Logging;

/// <summary>
/// Makes a value safe to write into a log line. A request can carry a carriage return or newline in a route
/// value, header, query string or body field; logged verbatim, that lets the caller start a fake log line
/// (log forging). Wrap every logged value that derives from a request:
/// <c>logger.LogInformation("approval.rejected id={Id}", LogSafe.Clean(id))</c>.
/// The two <c>Replace</c> calls are written out literally on purpose: they are the sanitiser CodeQL's
/// <c>cs/log-forging</c> query recognises. Typed values (an <c>int</c>, a <c>Guid</c> parsed from the route)
/// cannot carry a line break and do not need it.
/// </summary>
public static class LogSafe
{
    /// <summary>Longest value written to a log line.</summary>
    public const int MaxLength = 200;

    /// <summary>The value's text with CR and LF removed, cut to <paramref name="limit"/> characters.</summary>
    public static string Clean(object? value, int limit = MaxLength)
    {
        var text = (value?.ToString() ?? string.Empty)
            .Replace("\r", string.Empty, StringComparison.Ordinal)
            .Replace("\n", string.Empty, StringComparison.Ordinal);
        return text.Length > limit ? text[..limit] + "..." : text;
    }
}
