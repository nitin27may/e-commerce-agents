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

    // 16-digit card numbers with optional separators. The lookarounds keep a match from
    // starting or ending inside a longer token (a UUID or an id with a long digit run), and
    // a match is masked only when it passes the Luhn check.
    private static readonly Regex CardPattern =
        new(@"(?<![0-9A-Za-z-])\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}(?![0-9A-Za-z-])", RegexOptions.Compiled);

    private static readonly Regex SsnPattern =
        new(@"(?<![0-9A-Za-z-])\d{3}-\d{2}-\d{4}(?![0-9A-Za-z-])", RegexOptions.Compiled);

    internal static bool LuhnValid(string number)
    {
        var total = 0;
        var count = 0;
        for (var i = number.Length - 1; i >= 0; i--)
        {
            if (!char.IsDigit(number[i])) continue;
            var d = number[i] - '0';
            if (count % 2 == 1)
            {
                d *= 2;
                if (d > 9) d -= 9;
            }
            total += d;
            count++;
        }
        return count > 0 && total % 10 == 0;
    }

    public int Redactions { get; private set; }

    /// <summary>Returns the redacted text and increments the counter per match.</summary>
    public string Redact(string? input)
    {
        if (string.IsNullOrEmpty(input))
        {
            return input ?? string.Empty;
        }

        var cards = 0;
        var withCards = CardPattern.Replace(input, m =>
        {
            if (!LuhnValid(m.Value)) return m.Value;
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
