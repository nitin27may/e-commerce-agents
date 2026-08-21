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
}
