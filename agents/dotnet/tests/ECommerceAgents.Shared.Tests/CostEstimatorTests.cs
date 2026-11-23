using System.Text.Json;
using ECommerceAgents.Shared.Cost;
using FluentAssertions;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// Prices come only from <c>agents/python/config/pricing/models.yaml</c>, the file Python reads. The golden
/// cases in <c>config/pricing/testdata/cases.json</c> are run by both stacks, so the two cannot price the
/// same tokens differently. Tests that point <c>PRICING_FILE</c> elsewhere share one collection so they
/// never race the process-wide table.
/// </summary>
[Collection(PricingCollection.Name)]
public sealed class CostEstimatorTests : IDisposable
{
    private static readonly string PricingDir = Path.GetDirectoryName(CostEstimator.DefaultPricingFile()!)!;
    private static readonly string TestData = Path.Combine(PricingDir, "testdata");

    public CostEstimatorTests()
    {
        Environment.SetEnvironmentVariable("PRICING_FILE", null);
        Environment.SetEnvironmentVariable("PRICING_OVERRIDES_FILE", null);
        CostEstimator.ReloadPricing();
    }

    public void Dispose()
    {
        Environment.SetEnvironmentVariable("PRICING_FILE", null);
        Environment.SetEnvironmentVariable("PRICING_OVERRIDES_FILE", null);
        CostEstimator.ReloadPricing();
    }

    public static TheoryData<string> GoldenCaseNames()
    {
        var data = new TheoryData<string>();
        foreach (var c in LoadCases())
        {
            data.Add(c.GetProperty("name").GetString()!);
        }
        return data;
    }

    private static IEnumerable<JsonElement> LoadCases()
    {
        var path = Path.Combine(Path.GetDirectoryName(CostEstimator.DefaultPricingFile()!)!, "testdata", "cases.json");
        using var doc = JsonDocument.Parse(File.ReadAllText(path));
        return doc.RootElement.GetProperty("cases").EnumerateArray().Select(e => e.Clone()).ToList();
    }

    [Theory]
    [MemberData(nameof(GoldenCaseNames))]
    public void GoldenPricingCase(string name)
    {
        Environment.SetEnvironmentVariable("PRICING_FILE", Path.Combine(TestData, "prices.yaml"));
        CostEstimator.ReloadPricing();
        var c = LoadCases().Single(e => e.GetProperty("name").GetString() == name);

        var got = CostEstimator.Estimate(
            c.GetProperty("model").GetString(),
            c.GetProperty("tokens_in").GetInt32(),
            c.GetProperty("tokens_out").GetInt32(),
            c.GetProperty("cached_in").GetInt32());

        var expected = c.GetProperty("cost_usd");
        if (expected.ValueKind == JsonValueKind.Null)
        {
            got.Should().BeNull();
        }
        else
        {
            got.Should().NotBeNull();
            got!.Value.Should().BeApproximately(expected.GetDouble(), 1e-12);
        }
    }

    [Fact]
    public void TheShippedFile_PricesTheModelsTheStackDefaultsTo()
    {
        foreach (var model in new[] { "gpt-4.1", "gpt-5.1", "text-embedding-3-small" })
        {
            CostEstimator.Lookup(model).Should().NotBeNull(model);
        }
    }

    [Fact]
    public void AnUnlistedModel_IsUnknown_NotAGuess()
    {
        CostEstimator.Estimate("qwen2.5:14b", 1000, 1000).Should().BeNull();
    }

    [Fact]
    public void Overrides_AreMergedOverTheReferenceFile()
    {
        Environment.SetEnvironmentVariable("PRICING_FILE", Path.Combine(TestData, "prices.yaml"));
        var extra = Path.Combine(Path.GetTempPath(), $"pricing-{Guid.NewGuid():N}.yaml");
        File.WriteAllText(extra,
            "version: 1\nmodels:\n"
            + "  qwen2.5:14b: {input_per_1m: 0, output_per_1m: 0, source: local, as_of: null, verified: false}\n"
            + "  model-a: {input_per_1m: 1.0, output_per_1m: 1.0, source: negotiated, as_of: null, verified: false}\n");
        try
        {
            Environment.SetEnvironmentVariable("PRICING_OVERRIDES_FILE", extra);
            CostEstimator.ReloadPricing();

            CostEstimator.Estimate("qwen2.5:14b", 1000, 1000).Should().Be(0.0);
            CostEstimator.Estimate("model-a", 1_000_000, 0)!.Value.Should().BeApproximately(1.0, 1e-12);
            CostEstimator.Estimate("model-a-mini", 1000, 1000)!.Value.Should().BeApproximately(0.002, 1e-12);
        }
        finally
        {
            File.Delete(extra);
        }
    }

    [Fact]
    public void AnInvalidFile_MakesEveryCostUnknown()
    {
        var bad = Path.Combine(Path.GetTempPath(), $"pricing-{Guid.NewGuid():N}.yaml");
        File.WriteAllText(bad, "version: 1\nmodels:\n  m: {input_per_1m: -1, output_per_1m: 0}\n");
        try
        {
            Environment.SetEnvironmentVariable("PRICING_FILE", bad);
            CostEstimator.ReloadPricing();
            CostEstimator.Estimate("gpt-4.1", 1000, 1000).Should().BeNull();
        }
        finally
        {
            File.Delete(bad);
        }
    }

    [Theory]
    [InlineData("version: 2\nmodels: {}\n")]
    [InlineData("version: 1\nunit: per_1k_tokens\nmodels: {}\n")]
    [InlineData("version: 1\nmodels:\n  m: {input_per_1m: true, output_per_1m: 1}\n")]
    [InlineData("version: 1\nmodels:\n  m: {output_per_1m: 1}\n")]
    [InlineData("version: 1\nmodels:\n  M: {input_per_1m: 1, output_per_1m: 1}\n  m: {input_per_1m: 1, output_per_1m: 1}\n")]
    public void InvalidDocuments_AreRefused(string yaml)
    {
        var act = () => CostEstimator.Parse(yaml);
        act.Should().Throw<PricingException>();
    }

    [Fact]
    public void Estimate_UnpricedModel_LogsAFixedLineOnce()
    {
        CostEstimator.ResetUnpricedLogForTests();
        var logger = new CapturingLogger();

        CostEstimator.Estimate("my-private-deployment", 10, 10, logger: logger);
        CostEstimator.Estimate("my-private-deployment", 10, 10, logger: logger);

        logger.Messages.Should().Equal("cost.model_unpriced — cost is unknown; add the model to config/pricing/models.yaml");
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
        columns.CostUsd!.Value.Should().BeApproximately(CostEstimator.Estimate("gpt-4.1", 500, 50, 200)!.Value, 1e-12);
    }

    [Fact]
    public void PriceSource_NamesTheShippedFileWithAContentDigest_AndIsNullForAnUnpricedModel()
    {
        var source = CostEstimator.PriceSource("gpt-4.1");
        source.Should().MatchRegex("^models\\.yaml#[0-9a-f]{10}$");
        CostEstimator.PriceSource("gpt-4.1-2025-04-14").Should().Be(source);
        CostEstimator.PriceSource("my-private-deployment").Should().BeNull();
        CostEstimator.PriceSource("").Should().BeNull();
    }

    [Fact]
    public void PriceSource_NamesAnOverrideFileForItsOwnModels()
    {
        var path = Path.Combine(Path.GetTempPath(), $"mine-{Guid.NewGuid():N}.yaml");
        File.WriteAllText(path, "version: 1\nmodels:\n  local-llama:\n    input_per_1m: 0\n    output_per_1m: 0\n");
        try
        {
            Environment.SetEnvironmentVariable("PRICING_OVERRIDES_FILE", path);
            CostEstimator.ReloadPricing();
            CostEstimator.PriceSource("local-llama").Should().StartWith($"override:{Path.GetFileName(path)}#");
            CostEstimator.PriceSource("gpt-4.1").Should().StartWith("models.yaml#");
        }
        finally
        {
            File.Delete(path);
        }
    }

    [Fact]
    public void SummarizeRun_CarriesPriceSourceAndTimeOnlyWhenAPriceWasApplied()
    {
        var priced = new ECommerceAgents.Shared.Context.RunUsage();
        priced.Add("orchestrator", new ECommerceAgents.Shared.Context.SpecialistUsage(500, 50, 0, "gpt-4.1"));
        var columns = CostEstimator.SummarizeRun(priced, noLlmCalls: false);
        columns.PriceSource.Should().Be(CostEstimator.PriceSource("gpt-4.1"));
        columns.PricedAt.Should().NotBeNull();

        var unpriced = new ECommerceAgents.Shared.Context.RunUsage();
        unpriced.Add("orchestrator", new ECommerceAgents.Shared.Context.SpecialistUsage(500, 50, 0, "my-private-deployment"));
        var unknown = CostEstimator.SummarizeRun(unpriced, noLlmCalls: false);
        (unknown.CostUsd, unknown.PriceSource, unknown.PricedAt).Should().Be(((double?)null, (string?)null, (DateTimeOffset?)null));

        var free = CostEstimator.SummarizeRun(new ECommerceAgents.Shared.Context.RunUsage(), noLlmCalls: true);
        (free.CostUsd, free.PriceSource, free.PricedAt).Should().Be((0.0, (string?)null, (DateTimeOffset?)null));
    }

    [Fact]
    public void OneUnpricedAgent_MakesTheRunCostUnknown()
    {
        var run = new ECommerceAgents.Shared.Context.RunUsage();
        run.Add("orchestrator", new ECommerceAgents.Shared.Context.SpecialistUsage(1000, 0, 0, "gpt-4.1"));
        run.Add("product-discovery", new ECommerceAgents.Shared.Context.SpecialistUsage(1000, 0, 0, "my-private-deployment"));
        CostEstimator.EstimateRun(run.ByAgent).Should().BeNull();
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

/// <summary>Serializes every test that changes the process-wide pricing table.</summary>
[CollectionDefinition(Name, DisableParallelization = true)]
public sealed class PricingCollection
{
    public const string Name = "pricing";
}
