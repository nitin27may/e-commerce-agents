using System.Text.RegularExpressions;

namespace ECommerceAgents.Shared.Guardrails;

/// <summary>
/// Neutralize stored / indirect prompt injection in untrusted text — the
/// .NET twin of Python's <c>shared/guardrails/sanitize.py</c>.
/// </summary>
/// <remarks>
/// Tool results re-enter the model as function-result content. Content that
/// originates from other users — product reviews, descriptions, order notes
/// — can carry adversarial instructions ("ignore previous instructions",
/// role reassignments, fake system turns, system-prompt exfiltration). This
/// class defangs those markers (replaces them with an inert
/// <c>[neutralized]</c> token rather than deleting them, so legitimate
/// analysis still sees the text existed).
///
/// Pure functions: no I/O, no LLM, no DB. Patterns are deliberately high
/// precision (low false-positive) — the prompt-layer rules are the other
/// layer of defense. Patterns copied verbatim from the Python source so the
/// two stacks flag the same inputs.
/// </remarks>
public static class Sanitize
{
    private const string Mark = "[neutralized]";

    // Codepoints to strip: C0 controls except TAB (0x09) / LF (0x0A) / CR (0x0D),
    // DEL, the zero-width marks, line/paragraph separators, and the BOM —
    // used to smuggle hidden instructions past a naive text scan. Same set as
    // Python's sanitize.py, declared numerically for the same reason: the
    // source file never contains raw control bytes.
    private static readonly HashSet<int> StripCodepoints = BuildStripCodepoints();

    private static HashSet<int> BuildStripCodepoints()
    {
        var set = new HashSet<int>();
        for (var c = 0x00; c < 0x09; c++) set.Add(c);
        set.Add(0x0B);
        set.Add(0x0C);
        for (var c = 0x0E; c < 0x20; c++) set.Add(c);
        set.Add(0x7F);
        for (var c = 0x200B; c < 0x2010; c++) set.Add(c);
        set.Add(0x2028);
        set.Add(0x2029);
        set.Add(0xFEFF);
        return set;
    }

    private static readonly Regex[] InjectionPatterns =
    [
        new(
            @"ignore\s+(?:all\s+|any\s+)?(?:the\s+)?(?:previous|prior|above|earlier)\s+" +
            @"(?:instructions?|prompts?|rules?|messages?)",
            RegexOptions.IgnoreCase | RegexOptions.Compiled
        ),
        new(
            @"disregard\s+(?:all\s+|any\s+)?(?:the\s+)?(?:previous|prior|above|earlier)\s+" +
            @"(?:instructions?|prompts?|rules?)",
            RegexOptions.IgnoreCase | RegexOptions.Compiled
        ),
        new(
            @"forget\s+(?:all\s+|everything\s+|your\s+)?(?:previous\s+|prior\s+)?" +
            @"(?:instructions?|rules?)",
            RegexOptions.IgnoreCase | RegexOptions.Compiled
        ),
        new(@"you\s+are\s+now\s+(?:a|an|the)\b", RegexOptions.IgnoreCase | RegexOptions.Compiled),
        new(@"new\s+(?:system\s+)?(?:instructions?|prompts?|rules?)\s*:", RegexOptions.IgnoreCase | RegexOptions.Compiled),
        new(@"^\s{0,6}(?:system|developer|assistant)\s*:", RegexOptions.IgnoreCase | RegexOptions.Compiled | RegexOptions.Multiline),
        new(@"reveal\s+(?:your\s+|the\s+)?(?:system\s+)?(?:prompt|instructions?)", RegexOptions.IgnoreCase | RegexOptions.Compiled),
        new(@"</?\s*(?:system|instructions?|prompt)\s*>", RegexOptions.IgnoreCase | RegexOptions.Compiled),
        new(@"\bact\s+as\s+(?:if\s+you\s+are\s+)?(?:an?\s+)?admin", RegexOptions.IgnoreCase | RegexOptions.Compiled),
    ];

    /// <summary>Returns true if <paramref name="text"/> matches any high-precision injection signal.</summary>
    public static bool ContainsInjectionMarkers(string? text) =>
        !string.IsNullOrEmpty(text) && InjectionPatterns.Any(p => p.IsMatch(text));

    /// <summary>Strips smuggled control/zero-width characters and defangs injection markers.</summary>
    public static string NeutralizeText(string? text)
    {
        if (string.IsNullOrEmpty(text))
        {
            return text ?? string.Empty;
        }

        var stripped = new System.Text.StringBuilder(text.Length);
        foreach (var ch in text)
        {
            if (!StripCodepoints.Contains(ch))
            {
                stripped.Append(ch);
            }
        }

        var cleaned = stripped.ToString();
        foreach (var pattern in InjectionPatterns)
        {
            cleaned = pattern.Replace(cleaned, Mark);
        }
        return cleaned;
    }
}
