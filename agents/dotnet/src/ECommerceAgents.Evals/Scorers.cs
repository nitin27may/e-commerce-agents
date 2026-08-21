using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Grounding;

namespace ECommerceAgents.Evals;

/// <summary>
/// Deterministic scorers — no LLM, no cost, safe in CI.
/// </summary>
/// <remarks>
/// Python's original groundedness scorer returned 1.0 if <em>any</em> tool was called,
/// which meant a fabricated price scored identically to a real one; the whole point of
/// <see cref="GroundednessAsync"/> is that it compares the answer to the database.
/// </remarks>
public static class Scorers
{
    /// <summary>
    /// Fraction of the answer's checkable claims that the database confirms.
    /// </summary>
    /// <remarks>
    /// Reuses the production verifier rather than reimplementing it, so a scoring change
    /// cannot silently diverge from what the app actually enforces at runtime.
    ///
    /// Returns 1.0 when the answer claims nothing checkable. That is deliberate and worth
    /// stating: "I couldn't find any laptops" is a perfectly good answer and should not be
    /// punished for containing no product ids. Whether it *should* have found something is
    /// what <see cref="Correctness"/> measures.
    ///
    /// Expect .NET's numbers to sit below Python's on identical answers: Python resolves
    /// prose figures against a per-request ledger of tool results that .NET has no
    /// equivalent of, so a restated price that Python verifies comes back unverifiable
    /// here. Baselines must be recorded per stack, never copied across.
    /// </remarks>
    public static async Task<double> GroundednessAsync(string text, DatabasePool pool)
    {
        var claims = ClaimExtractor.Extract(text);
        if (claims.Total == 0)
        {
            return 1.0;
        }

        var report = await new GroundingVerifier(pool).VerifyAsync(claims);
        return report.Total == 0 ? 1.0 : (double)report.Verified / report.Total;
    }

    /// <summary>
    /// Fraction of the expected tools that were actually called.
    /// </summary>
    /// <remarks>
    /// Partial credit, and no penalty for extra calls — an agent that consults more than
    /// the minimum is not wrong, it is thorough. Matching ignores case and underscores
    /// because the same tool is <c>search_products</c> in Python and
    /// <c>SearchProducts</c> in .NET; forking the datasets to carry both spellings would
    /// guarantee they drift.
    /// </remarks>
    public static double Correctness(IReadOnlyList<string> expected, IReadOnlyList<string> called)
    {
        if (expected.Count == 0)
        {
            return 1.0;
        }

        var actual = called.Select(Normalize).ToHashSet(StringComparer.Ordinal);
        var matched = expected.Count(e => actual.Contains(Normalize(e)));
        return (double)matched / expected.Count;
    }

    /// <summary>
    /// Routing score for orchestrator cases: did the right specialist get the question?
    /// </summary>
    /// <remarks>
    /// Three-valued rather than binary, matching Python: reaching the wrong specialist is
    /// a different failure from reaching none at all — the first is a routing mistake, the
    /// second is the router not working.
    /// </remarks>
    public static double Routing(string expectedRoute, IReadOnlyList<string> routes)
    {
        if (routes.Count == 0)
        {
            return 0.0;
        }
        return routes.Any(r => Normalize(r) == Normalize(expectedRoute)) ? 1.0 : 0.5;
    }

    /// <summary>
    /// Fraction of the expected fields the answer actually mentions.
    /// </summary>
    /// <remarks>
    /// Keyword matching with a small alias table, the same shape as Python's. It is a weak
    /// signal and weighted lowest (0.2) for that reason: "price" counts as present if the
    /// text contains a "$". An LLM judge does this better and costs money, which is why
    /// this is what runs in the free CI pass.
    /// </remarks>
    public static (double Score, List<string> Missing) Completeness(
        IReadOnlyList<string> expectedFields,
        string text
    )
    {
        if (expectedFields.Count == 0)
        {
            return (1.0, []);
        }

        var haystack = text.ToLowerInvariant();
        var missing = new List<string>();

        foreach (var field in expectedFields)
        {
            if (!Mentions(haystack, field))
            {
                missing.Add(field);
            }
        }

        return ((double)(expectedFields.Count - missing.Count) / expectedFields.Count, missing);
    }

    private static readonly Dictionary<string, string[]> FieldAliases = new(StringComparer.OrdinalIgnoreCase)
    {
        ["price"] = ["price", "$", "cost"],
        ["name"] = ["name"],
        ["rating"] = ["rating", "star", "★", "out of 5"],
        ["stock"] = ["stock", "in stock", "available", "units"],
        ["status"] = ["status", "shipped", "delivered", "processing", "placed"],
        ["total"] = ["total", "$"],
        ["order_id"] = ["order", "#"],
        ["tracking_number"] = ["tracking", "trk"],
        ["discount"] = ["discount", "%", "off", "save"],
        ["warehouse"] = ["warehouse", "location"],
        ["sentiment"] = ["sentiment", "positive", "negative", "mixed"],
        ["eta"] = ["eta", "arriv", "deliver", "day"],
    };

    private static bool Mentions(string haystack, string field)
    {
        if (FieldAliases.TryGetValue(field, out var aliases))
        {
            return aliases.Any(a => haystack.Contains(a, StringComparison.OrdinalIgnoreCase));
        }
        return haystack.Contains(field.Replace('_', ' '), StringComparison.OrdinalIgnoreCase)
            || haystack.Contains(field, StringComparison.OrdinalIgnoreCase);
    }

    private static string Normalize(string name) =>
        name.Replace("_", "", StringComparison.Ordinal).ToLowerInvariant();
}
