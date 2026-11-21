using Microsoft.Extensions.Logging;

namespace ECommerceAgents.Shared.Cost;

/// <summary>
/// USD per 1K tokens for one model. <paramref name="CachedInputPer1K"/> is the provider's
/// cached-input rate; <c>null</c> means none is on file and cached tokens are billed as input
/// (the estimate then errs high, never low).
/// </summary>
public readonly record struct ModelPricing(double InputPer1K, double OutputPer1K, double? CachedInputPer1K = null);

/// <summary>
/// Token-to-dollar estimation — the .NET twin of Python's <c>shared/cost.py</c>.
///
/// .NET already recorded <c>tokens_in</c>/<c>tokens_out</c> via
/// <c>UsageRecorder</c>, but had no way to turn them into money: no pricing
/// table, and so no ceiling either. That left an agentic loop on the .NET side
/// with no spend limit at all (issue #30).
/// </summary>
public static class CostEstimator
{
    // Keys lowercase — callers pass through whatever LLM_MODEL or
    // AZURE_OPENAI_DEPLOYMENT is configured, and deployment names are
    // conventionally lowercase. Kept in sync with shared/cost.py.
    //
    // CachedInputPer1K is deliberately unset on every entry: the repo documents no cached-input
    // rate or discount ratio, and a price is not something to infer. A maintainer fills it in
    // from the provider's pricing page (the "cached input" column of OpenAI's API pricing, or
    // Azure OpenAI's pricing page for an Azure deployment), one number per model.
    private static readonly IReadOnlyDictionary<string, ModelPricing> Pricing =
        new Dictionary<string, ModelPricing>(StringComparer.OrdinalIgnoreCase)
        {
            ["gpt-4.1"] = new(0.002, 0.008),
            ["gpt-4.1-mini"] = new(0.0004, 0.0016),
            ["gpt-4.1-nano"] = new(0.0001, 0.0004),
            // ESTIMATES, not confirmed list prices (placeholders until checked against a real
            // deployment); "gpt-5.1-mini" is a plausible name, not a confirmed model id.
            ["gpt-5.1"] = new(0.00125, 0.01),
            ["gpt-5.1-mini"] = new(0.00025, 0.002),
            ["gpt-4o"] = new(0.0025, 0.01),
            ["gpt-4o-mini"] = new(0.00015, 0.0006),
            ["text-embedding-3-small"] = new(0.00002, 0.0),
            ["text-embedding-3-large"] = new(0.00013, 0.0),
        };

    // gpt-4.1 pricing, matching AgentSettings.LlmModel's own default — an
    // unrecognized or custom deployment name still gets a reasonable estimate
    // rather than silently pricing at zero, which would make a budget ceiling
    // quietly unenforceable.
    private static readonly ModelPricing DefaultPricing = Pricing["gpt-4.1"];

    private static int _fallbackLogged;

    /// <summary>
    /// Exact match, else the longest priced key the model is a dated snapshot of
    /// (<c>gpt-4.1-2025-04-14</c> prices as <c>gpt-4.1</c>; only a <c>-&lt;digit&gt;</c> suffix
    /// counts, so <c>gpt-4.1-mini</c> never resolves to <c>gpt-4.1</c>).
    /// </summary>
    private static ModelPricing? Lookup(string? model)
    {
        var name = model?.Trim().ToLowerInvariant();
        if (string.IsNullOrEmpty(name))
        {
            return null;
        }

        if (Pricing.TryGetValue(name, out var exact))
        {
            return exact;
        }

        foreach (var key in Pricing.Keys.OrderByDescending(k => k.Length))
        {
            if (name.StartsWith(key, StringComparison.Ordinal)
                && name.Length > key.Length + 1
                && name[key.Length] == '-'
                && char.IsAsciiDigit(name[key.Length + 1]))
            {
                return Pricing[key];
            }
        }

        return null;
    }

    /// <summary>
    /// Estimated USD for a single call. Never throws on an unknown model: it prices at the
    /// default entry and, when a logger is given, says so once per process (fixed text, no
    /// request data).
    /// </summary>
    /// <param name="tokensIn">Total input, cached included, as providers report it.</param>
    /// <param name="cachedIn">The cached share of <paramref name="tokensIn"/>.</param>
    public static double Estimate(
        string? model, int tokensIn, int tokensOut, int cachedIn = 0,
        Microsoft.Extensions.Logging.ILogger? logger = null)
    {
        var pricing = Lookup(model);
        if (pricing is null)
        {
            if (logger is not null && Interlocked.Exchange(ref _fallbackLogged, 1) == 0)
            {
                logger.LogWarning("cost.model_unpriced — using the default price entry");
            }

            pricing = DefaultPricing;
        }

        var cached = Math.Clamp(cachedIn, 0, Math.Max(tokensIn, 0));
        var uncached = Math.Max(tokensIn, 0) - cached;
        var cachedRate = pricing.Value.CachedInputPer1K ?? pricing.Value.InputPer1K;
        return (uncached / 1000.0 * pricing.Value.InputPer1K)
            + (cached / 1000.0 * cachedRate)
            + (tokensOut / 1000.0 * pricing.Value.OutputPer1K);
    }

    /// <summary>Test hook: forget that the unpriced-model line was already logged.</summary>
    public static void ResetFallbackLogForTests() => Interlocked.Exchange(ref _fallbackLogged, 0);

    /// <summary>Estimated USD for one run, each agent priced at the model it reported.</summary>
    public static double EstimateRun(IReadOnlyDictionary<string, ECommerceAgents.Shared.Context.SpecialistUsage> byAgent) =>
        byAgent.Values.Sum(u => Estimate(
            u.Model,
            (int)Math.Min(u.InputTokenCount, int.MaxValue),
            (int)Math.Min(u.OutputTokenCount, int.MaxValue),
            (int)Math.Min(u.CachedInputTokenCount, int.MaxValue)));

    /// <summary>What a run's <c>usage_logs</c> row records beyond token counts.</summary>
    /// <remarks>
    /// All three are <c>null</c> when nothing was captured; a mode that makes no LLM call
    /// (<paramref name="noLlmCalls"/>) yields an explicit cost of 0 — so "free" and "unknown"
    /// are different rows. Twin of Python's <c>shared/usage_db.py::usage_columns</c>.
    /// </remarks>
    public readonly record struct RunColumns(string? Model, int? TokensCachedIn, double? CostUsd);

    public static RunColumns SummarizeRun(ECommerceAgents.Shared.Context.RunUsage usage, bool noLlmCalls)
    {
        var byAgent = usage.ByAgent;
        if (byAgent.Count == 0)
        {
            return noLlmCalls ? new RunColumns(null, 0, 0.0) : new RunColumns(null, null, null);
        }

        var heaviest = byAgent.Values.MaxBy(u => u.InputTokenCount)!;
        var cached = (int)Math.Min(byAgent.Values.Sum(u => u.CachedInputTokenCount), int.MaxValue);
        return new RunColumns(string.IsNullOrEmpty(heaviest.Model) ? null : heaviest.Model, cached, EstimateRun(byAgent));
    }
}
