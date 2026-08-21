namespace ECommerceAgents.Shared.Cost;

/// <summary>USD per 1K tokens for one model.</summary>
public readonly record struct ModelPricing(double InputPer1K, double OutputPer1K);

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
    private static readonly IReadOnlyDictionary<string, ModelPricing> Pricing =
        new Dictionary<string, ModelPricing>(StringComparer.OrdinalIgnoreCase)
        {
            ["gpt-4.1"] = new(0.002, 0.008),
            ["gpt-4.1-mini"] = new(0.0004, 0.0016),
            ["gpt-4.1-nano"] = new(0.0001, 0.0004),
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

    /// <summary>Estimated USD for a single call. Never throws on an unknown model.</summary>
    public static double Estimate(string? model, int tokensIn, int tokensOut)
    {
        var pricing = model is not null && Pricing.TryGetValue(model, out var found) ? found : DefaultPricing;
        return (tokensIn / 1000.0 * pricing.InputPer1K) + (tokensOut / 1000.0 * pricing.OutputPer1K);
    }
}
