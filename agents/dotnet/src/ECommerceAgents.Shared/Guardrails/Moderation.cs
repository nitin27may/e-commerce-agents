using System.Text.RegularExpressions;

namespace ECommerceAgents.Shared.Guardrails;

/// <summary>Content-policy categories a piece of text can be flagged under.</summary>
public enum ModerationCategory
{
    SelfHarm,
    Violence,
    HateHarassment,
    Sexual,
}

/// <summary>
/// Coarse local content-moderation classifier for outbound agent text — the
/// .NET twin of Python's <c>shared/guardrails/moderation.py</c>.
/// </summary>
/// <remarks>
/// Distinct from <see cref="Sanitize"/>: sanitization defangs adversarial
/// <em>instructions</em> hiding inside untrusted input (a review body
/// telling the model to "ignore previous instructions") so they never
/// influence the next turn. This class classifies the model's own
/// <em>output</em> text against content-policy categories (self-harm,
/// violence, hate/harassment, sexual content) — a different problem. An
/// agent that correctly resists every injection attempt can still generate
/// harmful text on its own.
///
/// Pure functions: no I/O, no LLM call, no external API. Deliberately a
/// small set of high-precision phrase patterns, mirroring
/// <see cref="Sanitize"/>'s own philosophy (low false-positive over
/// exhaustive recall) — this is a coarse first-pass filter, not a trained
/// classifier. Patterns copied verbatim from the Python source so the two
/// stacks flag the same text.
/// </remarks>
public static class Moderation
{
    private static readonly Dictionary<ModerationCategory, Regex[]> Patterns = new()
    {
        [ModerationCategory.SelfHarm] =
        [
            new(@"\bkill\s+(?:myself|yourself)\b", RegexOptions.IgnoreCase | RegexOptions.Compiled),
            new(@"\b(?:commit|committing)\s+suicide\b", RegexOptions.IgnoreCase | RegexOptions.Compiled),
            new(@"\bways?\s+to\s+(?:end|take)\s+(?:my|your|his|her|their)\s+(?:own\s+)?life\b", RegexOptions.IgnoreCase | RegexOptions.Compiled),
            new(@"\bself[\s-]harm\b", RegexOptions.IgnoreCase | RegexOptions.Compiled),
        ],
        [ModerationCategory.Violence] =
        [
            new(@"\bhow\s+to\s+(?:build|make)\s+a\s+(?:bomb|explosive|weapon)\b", RegexOptions.IgnoreCase | RegexOptions.Compiled),
            new(@"\bi\s+(?:will|'ll|am\s+going\s+to)\s+kill\s+you\b", RegexOptions.IgnoreCase | RegexOptions.Compiled),
            new(@"\bmass\s+shooting\b", RegexOptions.IgnoreCase | RegexOptions.Compiled),
        ],
        [ModerationCategory.HateHarassment] =
        [
            new(@"\ball\s+\w+\s+(?:people\s+)?(?:are|should\s+be)\s+(?:killed|exterminated|eliminated)\b", RegexOptions.IgnoreCase | RegexOptions.Compiled),
            new(@"\byou'?re\s+(?:a\s+)?(?:worthless|subhuman)\b", RegexOptions.IgnoreCase | RegexOptions.Compiled),
        ],
        [ModerationCategory.Sexual] =
        [
            new(@"\bsexually\s+explicit\s+(?:content|description|story)\b", RegexOptions.IgnoreCase | RegexOptions.Compiled),
            new(@"\bchild\s+sexual\s+abuse\b", RegexOptions.IgnoreCase | RegexOptions.Compiled),
        ],
    };

    /// <summary>Returns every category whose patterns match <paramref name="text"/>. Empty = clean.</summary>
    public static HashSet<ModerationCategory> Classify(string? text)
    {
        var hits = new HashSet<ModerationCategory>();
        if (string.IsNullOrEmpty(text))
        {
            return hits;
        }

        foreach (var (category, patterns) in Patterns)
        {
            if (patterns.Any(p => p.IsMatch(text)))
            {
                hits.Add(category);
            }
        }
        return hits;
    }
}
