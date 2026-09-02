using ECommerceAgents.Shared.Configuration;
using FluentAssertions;
using Microsoft.Extensions.Configuration;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// Covers a real bug found while live-verifying the .NET docker-compose
/// stack: <c>docker-compose.dotnet.yml</c> supplies Redis via
/// <c>ConnectionStrings__Redis</c> (double-underscore env-var binding,
/// same convention already used for Postgres), but the loader previously
/// read only the <c>REDIS_URL</c> env var — so the compose-supplied value
/// was silently never picked up. Mirrors <see cref="AgentSettings.DatabaseUrl"/>'s
/// existing (and already-correct) precedence.
/// </summary>
public sealed class AgentSettingsLoaderTests
{
    [Fact]
    public void Load_PrefersConnectionStringsRedis_OverRedisUrlEnvVar()
    {
        var config = new ConfigurationBuilder()
            .AddInMemoryCollection(new Dictionary<string, string?> { ["ConnectionStrings:Redis"] = "redis:6379" })
            .Build();

        var settings = AgentSettingsLoader.Load(config);

        settings.RedisUrl.Should().Be("redis:6379");
    }

    [Fact]
    public void Load_FallsBackToDefault_WhenNeitherIsSet()
    {
        var config = new ConfigurationBuilder().Build();

        var settings = AgentSettingsLoader.Load(config);

        settings.RedisUrl.Should().Be("redis://localhost:6379");
    }

    [Fact]
    public void Load_TemperatureDefaultsTo0_2_MirroringPython()
    {
        var config = new ConfigurationBuilder().Build();

        var settings = AgentSettingsLoader.Load(config);

        settings.Temperature.Should().Be(0.2);
    }

    [Fact]
    public void Load_ReadsTemperatureFromLlmTemperatureEnvVar()
    {
        var config = new ConfigurationBuilder()
            .AddInMemoryCollection(new Dictionary<string, string?> { ["LLM_TEMPERATURE"] = "0.7" })
            .Build();

        var settings = AgentSettingsLoader.Load(config);

        settings.Temperature.Should().Be(0.7);
    }

    private static AgentSettings LoadWithGroundingMode(string? mode)
    {
        var values = new Dictionary<string, string?>();
        if (mode is not null)
        {
            values["GROUNDING_MODE"] = mode;
        }
        return AgentSettingsLoader.Load(
            new ConfigurationBuilder().AddInMemoryCollection(values).Build()
        );
    }

    [Fact]
    public void Load_DefaultsGroundingModeToAnnotate_MatchingPython() =>
        LoadWithGroundingMode(null).GroundingMode.Should().Be("annotate");

    [Theory]
    [InlineData("off")]
    [InlineData("observe")]
    [InlineData("ANNOTATE")]
    public void Load_AcceptsTheGroundingModesDotnetImplements(string mode) =>
        LoadWithGroundingMode(mode).GroundingMode.Should().Be(mode.ToLowerInvariant());

    /// <summary>
    /// The strongest-sounding setting must not be the one that lies. Python's
    /// "enforce" strips unverified cards and corrects prices; .NET does not, so
    /// accepting the value would leave an operator believing a defense is on
    /// that isn't — the same failure this repo already fixed once, when
    /// GUARDRAILS_BLOCK_ON_INJECTION only raised a log level.
    /// </summary>
    [Fact]
    public void Load_RefusesGroundingModeEnforce_AndSaysWhereItExists()
    {
        var act = () => LoadWithGroundingMode("enforce");

        act.Should().Throw<InvalidOperationException>().WithMessage("*enforce*Python*");
    }

    [Fact]
    public void Load_RefusesAnUnknownGroundingMode() =>
        FluentActions.Invoking(() => LoadWithGroundingMode("strict"))
            .Should().Throw<InvalidOperationException>();

    /// <summary>
    /// A setting declared on <c>AgentSettings</c> but never read in
    /// <c>AgentSettingsLoader</c> compiles, runs, and silently ignores its
    /// environment variable — the property just keeps its C# default forever.
    /// </summary>
    /// <remarks>
    /// This has now happened twice. <c>HandoffMaxTurns</c> shipped bound on the
    /// Python side and unbound here, so <c>HANDOFF_MAX_TURNS</c> did nothing on
    /// .NET while appearing to work. It is the same shape as the tool-naming
    /// defect: a contract that exists in two places and is enforced in one.
    ///
    /// Reflection over the loader source rather than over the type, because the
    /// question is "does the loader read this name?" — which is a property of
    /// the wiring, not of the settings object.
    /// </remarks>
    [Fact]
    public void EveryEnvBackedSettingIsActuallyReadByTheLoader()
    {
        string loaderPath = FindRepoFile(
            Path.Combine("agents", "dotnet", "src", "ECommerceAgents.Shared", "Configuration", "AgentSettingsLoader.cs"));
        string loader = File.ReadAllText(loaderPath);

        // Settings whose value comes from the environment, paired with the
        // variable the loader must read. Deliberately hand-maintained: the point
        // is that adding a setting makes someone add a line here too.
        var envBacked = new Dictionary<string, string>
        {
            ["HandoffMaxTurns"] = "HANDOFF_MAX_TURNS",
            ["RateLimitMaxRequests"] = "RATE_LIMIT_MAX_REQUESTS",
            ["RateLimitEnabled"] = "RATE_LIMIT_ENABLED",
            ["CostBudgetMode"] = "COST_BUDGET_MODE",
            ["GroundingMode"] = "GROUNDING_MODE",
        };

        var unbound = envBacked
            .Where(kv => !loader.Contains($"{kv.Key} =") || !loader.Contains($"\"{kv.Value}\""))
            .Select(kv => $"{kv.Key} <- {kv.Value}")
            .ToList();

        unbound.Should().BeEmpty(
            "a setting the loader never reads keeps its C# default forever, and the "
            + "environment variable silently does nothing");
    }

    // ── ParseAgentRegistry ──────────────────────────────────────────────
    //
    // These pin the behaviour change that came with the Azure pre-work: the
    // parser used to return an empty dictionary on any malformed input, which
    // produced an orchestrator that started, passed health checks, and could
    // not route. Mirrors the Python tests in
    // agents/python/tests/test_config_loader.py, including which inputs are
    // rejected — a stack that accepts what the other rejects is a parity gap.

    [Fact]
    public void ParseAgentRegistry_AcceptsAManagedEndpointWithNoPort()
    {
        var settings = new AgentSettings
        {
            AgentRegistry = """{"review-sentiment":"https://rs.internal.azurecontainerapps.io"}""",
        };

        AgentSettingsLoader.ParseAgentRegistry(settings)
            .Should()
            .ContainKey("review-sentiment")
            .WhoseValue.Should()
            .Be("https://rs.internal.azurecontainerapps.io");
    }

    [Theory]
    [InlineData("")]
    [InlineData("   ")]
    [InlineData("{}")]
    public void ParseAgentRegistry_IsEmptyForBlankInput(string raw)
    {
        AgentSettingsLoader.ParseAgentRegistry(new AgentSettings { AgentRegistry = raw })
            .Should()
            .BeEmpty();
    }

    [Fact]
    public void ParseAgentRegistry_ThrowsOnMalformedJson()
    {
        var settings = new AgentSettings { AgentRegistry = "{not json" };

        FluentActions.Invoking(() => AgentSettingsLoader.ParseAgentRegistry(settings))
            .Should()
            .Throw<InvalidOperationException>()
            .WithMessage("*not valid JSON*");
    }

    [Fact]
    public void ParseAgentRegistry_ThrowsOnAnEmptyUrl()
    {
        var settings = new AgentSettings { AgentRegistry = """{"product-discovery":""}""" };

        FluentActions.Invoking(() => AgentSettingsLoader.ParseAgentRegistry(settings))
            .Should()
            .Throw<InvalidOperationException>()
            .WithMessage("*empty URL*");
    }

    [Fact]
    public void ParseAgentRegistry_ThrowsOnAUrlWithNoScheme()
    {
        var settings = new AgentSettings
        {
            AgentRegistry = """{"product-discovery":"product-discovery:8081"}""",
        };

        FluentActions.Invoking(() => AgentSettingsLoader.ParseAgentRegistry(settings))
            .Should()
            .Throw<InvalidOperationException>()
            .WithMessage("*absolute http(s) URL*");
    }

    [Fact]
    public void ParseAgentRegistry_NamesTheOffendingAgent()
    {
        var settings = new AgentSettings
        {
            AgentRegistry = """{"product-discovery":"http://pd:8081","order-management":""}""",
        };

        FluentActions.Invoking(() => AgentSettingsLoader.ParseAgentRegistry(settings))
            .Should()
            .Throw<InvalidOperationException>()
            .WithMessage("*order-management*");
    }

    private static string FindRepoFile(string relative)
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !File.Exists(Path.Combine(dir.FullName, relative)))
        {
            dir = dir.Parent;
        }

        dir.Should().NotBeNull($"could not locate {relative} from {AppContext.BaseDirectory}");
        return Path.Combine(dir!.FullName, relative);
    }
}
