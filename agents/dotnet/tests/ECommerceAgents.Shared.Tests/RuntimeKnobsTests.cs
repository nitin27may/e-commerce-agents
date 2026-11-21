using ECommerceAgents.Shared.Agents;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Cost;
using ECommerceAgents.Shared.Data;
using FluentAssertions;
using Microsoft.Extensions.Configuration;
using Npgsql;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// Runtime knobs for managed hosting (#89 P2): optional <c>LLM_TEMPERATURE</c>, pool bounds,
/// <c>sslmode</c> and the gpt-5.1 price rows. Python parity: <c>tests/test_runtime_knobs.py</c>.
/// </summary>
[Collection(nameof(ProcessEnvironmentCollection))]
public sealed class RuntimeKnobsTests
{
    private static AgentSettings Load(params (string Key, string Value)[] values) =>
        AgentSettingsLoader.Load(
            new ConfigurationBuilder()
                .AddInMemoryCollection(values.ToDictionary(v => v.Key, v => (string?)v.Value))
                .Build());

    [Theory]
    [InlineData("")]
    [InlineData("   ")]
    public void BlankTemperature_LoadsAsNull(string raw)
    {
        Load(("LLM_TEMPERATURE", raw)).Temperature.Should().BeNull();
    }

    [Fact]
    public void BuildOptions_OmitsTemperature_WhenNull()
    {
        var options = SpecialistAgentFactory.BuildOptions(
            new AgentSettings { Temperature = null }, "be helpful", "order-management");

        options.ChatOptions!.Temperature.Should().BeNull();
    }

    [Fact]
    public void BuildOptions_DefaultPath_StillPinsTemperature()
    {
        var options = SpecialistAgentFactory.BuildOptions(
            Load(), "be helpful", "order-management");

        options.ChatOptions!.Temperature.Should().Be(0.2f);
    }

    [Fact]
    public void PoolBounds_DefaultToUnset_SoNpgsqlDefaultsStand()
    {
        var settings = Load();
        settings.DbPoolMinSize.Should().BeNull();
        settings.DbPoolMaxSize.Should().BeNull();
        PostgresConnectionString.WithPoolBounds("Host=db", null, null).Should().Be("Host=db");
    }

    [Fact]
    public void PoolBounds_ParseFromEnvironment()
    {
        var settings = Load(("DB_POOL_MIN_SIZE", "1"), ("DB_POOL_MAX_SIZE", "4"));
        settings.DbPoolMinSize.Should().Be(1);
        settings.DbPoolMaxSize.Should().Be(4);

        var built = new NpgsqlConnectionStringBuilder(
            PostgresConnectionString.WithPoolBounds("Host=db", settings.DbPoolMinSize, settings.DbPoolMaxSize));
        built.MinPoolSize.Should().Be(1);
        built.MaxPoolSize.Should().Be(4);
    }

    [Fact]
    public void PoolBounds_RejectNonNumeric()
    {
        var act = () => Load(("DB_POOL_MAX_SIZE", "lots"));
        act.Should().Throw<InvalidOperationException>();
    }

    [Theory]
    [InlineData(9, 4)]
    [InlineData(0, 0)]
    [InlineData(-1, 4)]
    public void PoolBounds_RejectInvalidCombinations(int min, int max)
    {
        var act = () => PostgresConnectionString.WithPoolBounds("Host=db", min, max);
        act.Should().Throw<InvalidOperationException>();
    }

    [Fact]
    public void FromUrl_WithoutSslMode_LeavesNpgsqlDefault()
    {
        var plain = new NpgsqlConnectionStringBuilder(
            PostgresConnectionString.FromUrl("postgresql://u:p@host:5432/db"));
        var untouched = new NpgsqlConnectionStringBuilder { Host = "host", Port = 5432, Username = "u", Password = "p", Database = "db" };

        plain.SslMode.Should().Be(untouched.SslMode);
    }

    [Theory]
    [InlineData("disable", SslMode.Disable)]
    [InlineData("allow", SslMode.Allow)]
    [InlineData("prefer", SslMode.Prefer)]
    [InlineData("require", SslMode.Require)]
    [InlineData("verify-ca", SslMode.VerifyCA)]
    [InlineData("verify-full", SslMode.VerifyFull)]
    [InlineData("REQUIRE", SslMode.Require)]
    public void FromUrl_MapsSslMode(string value, SslMode expected)
    {
        var parsed = new NpgsqlConnectionStringBuilder(
            PostgresConnectionString.FromUrl($"postgresql://u:p@host:5432/db?sslmode={value}"));

        parsed.SslMode.Should().Be(expected);
        parsed.Host.Should().Be("host");
        parsed.Database.Should().Be("db");
    }

    [Fact]
    public void FromUrl_SslModeAmongOtherParameters()
    {
        var parsed = new NpgsqlConnectionStringBuilder(
            PostgresConnectionString.FromUrl("postgresql://u:p@host/db?application_name=x&sslmode=verify-full"));

        parsed.SslMode.Should().Be(SslMode.VerifyFull);
    }

    [Fact]
    public void FromUrl_UnknownSslMode_Throws()
    {
        var act = () => PostgresConnectionString.FromUrl("postgresql://u:p@host/db?sslmode=bogus");
        act.Should().Throw<ArgumentException>().WithMessage("*bogus*");
    }

    [Fact]
    public void Gpt51_HasItsOwnPriceRows()
    {
        CostEstimator.Estimate("gpt-5.1", 1000, 0).Should().NotBe(CostEstimator.Estimate("gpt-4.1", 1000, 0));
        CostEstimator.Estimate("gpt-5.1-2025-11-13", 1000, 1000)
            .Should().Be(CostEstimator.Estimate("gpt-5.1", 1000, 1000));
        CostEstimator.Estimate("gpt-5.1-mini", 1000, 1000)!.Value
            .Should().BeLessThan(CostEstimator.Estimate("gpt-5.1", 1000, 1000)!.Value);
    }
}
