using ECommerceAgents.Shared.Cost;
using FluentAssertions;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// Issue #30 — .NET logged tokens but had no way to price them, so a budget
/// ceiling was not expressible. Values mirror <c>shared/cost.py</c>.
/// </summary>
public sealed class CostEstimatorTests
{
    [Fact]
    public void Estimate_UsesPerModelPricing()
    {
        // gpt-4.1: $0.002 / 1K in, $0.008 / 1K out
        CostEstimator.Estimate("gpt-4.1", 1000, 1000).Should().BeApproximately(0.010, 1e-9);
        CostEstimator.Estimate("gpt-4.1", 500, 0).Should().BeApproximately(0.001, 1e-9);
    }

    [Fact]
    public void Estimate_IsCaseInsensitive_BecauseDeploymentNamesVary()
    {
        // Callers pass through whatever LLM_MODEL or AZURE_OPENAI_DEPLOYMENT is
        // configured, and those are free-form strings.
        CostEstimator.Estimate("GPT-4.1", 1000, 1000)
            .Should().Be(CostEstimator.Estimate("gpt-4.1", 1000, 1000));
    }

    /// <summary>
    /// An unknown model must not price at zero — that would make a ceiling
    /// silently unenforceable for exactly the custom deployment names this
    /// repo encourages (Ollama, LM Studio, OpenRouter via LLM_BASE_URL).
    /// </summary>
    [Fact]
    public void Estimate_UnknownModel_FallsBackToDefaultPricingRatherThanZero()
    {
        var unknown = CostEstimator.Estimate("qwen2.5:14b", 1000, 1000);

        unknown.Should().BeGreaterThan(0);
        unknown.Should().Be(CostEstimator.Estimate("gpt-4.1", 1000, 1000));
    }

    [Fact]
    public void Estimate_EmbeddingModels_ChargeInputOnly()
    {
        CostEstimator.Estimate("text-embedding-3-small", 1000, 1000)
            .Should().BeApproximately(0.00002, 1e-9, "embeddings have no output tokens to bill");
    }

    [Fact]
    public void Estimate_ZeroTokens_IsFree()
    {
        CostEstimator.Estimate("gpt-4.1", 0, 0).Should().Be(0);
    }
}
