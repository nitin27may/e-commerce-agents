using System.Text.RegularExpressions;

namespace ECommerceAgents.Shared.Middleware;

/// <summary>
/// Masks credit-card- and SSN-shaped substrings. Mirrors Python's
/// <c>PiiRedactionMiddleware</c>. Stateless except for a cheap
/// running counter the host can sample for health dashboards.
/// </summary>
public sealed class PiiRedactor
{
    public const string CardMask = "[REDACTED-CARD]";
    public const string SsnMask = "[REDACTED-SSN]";

    // 16-digit card numbers with optional separators. Loose on purpose
    // so test messages without Luhn checks still mask.
    private static readonly Regex CardPattern =
        new(@"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b", RegexOptions.Compiled);

    private static readonly Regex SsnPattern =
        new(@"\b\d{3}-\d{2}-\d{4}\b", RegexOptions.Compiled);

    public int Redactions { get; private set; }

    /// <summary>Returns the redacted text and increments the counter per match.</summary>
    public string Redact(string? input)
    {
        if (string.IsNullOrEmpty(input))
        {
            return input ?? string.Empty;
        }

        var cards = 0;
        var withCards = CardPattern.Replace(input, _ =>
        {
            cards++;
            return CardMask;
        });

        var ssns = 0;
        var withSsn = SsnPattern.Replace(withCards, _ =>
        {
            ssns++;
            return SsnMask;
        });

        Redactions += cards + ssns;
        return withSsn;
    }
}
