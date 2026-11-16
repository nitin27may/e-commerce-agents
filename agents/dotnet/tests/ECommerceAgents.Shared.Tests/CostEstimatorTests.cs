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

    // ─────────────────────── E-2d: cached input, snapshots, run summary ───────────────────────

    [Fact]
    public void Estimate_CachedInput_IsBilledAsInput_WhenNoCachedRateIsOnFile()
    {
        // No cached rate is documented in the repo, so none is invented: the estimate errs high.
        CostEstimator.Estimate("gpt-4.1", 1000, 0, cachedIn: 900)
            .Should().Be(CostEstimator.Estimate("gpt-4.1", 1000, 0));
    }

    [Fact]
    public void Estimate_DatedSnapshot_PricesAsItsBaseModel_ButNeverAsADifferentModel()
    {
        CostEstimator.Estimate("gpt-4.1-2025-04-14", 1000, 1000).Should().Be(CostEstimator.Estimate("gpt-4.1", 1000, 1000));
        CostEstimator.Estimate("gpt-4.1-mini-2025-04-14", 1000, 1000).Should().Be(CostEstimator.Estimate("gpt-4.1-mini", 1000, 1000));
        CostEstimator.Estimate("gpt-4.1-mini", 1000, 1000).Should().NotBe(CostEstimator.Estimate("gpt-4.1", 1000, 1000));
    }

    [Fact]
    public void SummarizeRun_DistinguishesNotCapturedFromNoLlmCalls()
    {
        var empty = new ECommerceAgents.Shared.Context.RunUsage();
        CostEstimator.SummarizeRun(empty, noLlmCalls: false).Should().Be(new CostEstimator.RunColumns(null, null, null));
        CostEstimator.SummarizeRun(empty, noLlmCalls: true).Should().Be(new CostEstimator.RunColumns(null, 0, 0.0));

        var run = new ECommerceAgents.Shared.Context.RunUsage();
        run.Add("orchestrator", new ECommerceAgents.Shared.Context.SpecialistUsage(500, 50, 200, "gpt-4.1"));
        var columns = CostEstimator.SummarizeRun(run, noLlmCalls: false);
        columns.Model.Should().Be("gpt-4.1");
        columns.TokensCachedIn.Should().Be(200);
        columns.CostUsd.Should().BeApproximately(CostEstimator.Estimate("gpt-4.1", 500, 50, 200), 1e-12);
    }

    [Fact]
    public void Estimate_UnpricedModel_LogsAFixedLineOnce()
    {
        CostEstimator.ResetFallbackLogForTests();
        var logger = new CapturingLogger();

        CostEstimator.Estimate("my-private-deployment", 10, 10, logger: logger);
        CostEstimator.Estimate("my-private-deployment", 10, 10, logger: logger);

        logger.Messages.Should().Equal("cost.model_unpriced — using the default price entry");
    }

    private sealed class CapturingLogger : Microsoft.Extensions.Logging.ILogger
    {
        public List<string> Messages { get; } = [];

        public IDisposable? BeginScope<TState>(TState state) where TState : notnull => null;

        public bool IsEnabled(Microsoft.Extensions.Logging.LogLevel logLevel) => true;

        public void Log<TState>(
            Microsoft.Extensions.Logging.LogLevel logLevel, Microsoft.Extensions.Logging.EventId eventId,
            TState state, Exception? exception, Func<TState, Exception?, string> formatter) =>
            Messages.Add(formatter(state, exception));
    }
}
