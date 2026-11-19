using ECommerceAgents.Shared.Configuration;
using FluentAssertions;
using Microsoft.Extensions.Configuration;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>The A2A settings: the same names, defaults and validation as Python's <c>shared/config.py</c>.</summary>
[Collection(nameof(ProcessEnvironmentCollection))]
public sealed class A2ASettingsTests
{
    private static AgentSettings Load(params (string Key, string Value)[] values) =>
        AgentSettingsLoader.Load(new ConfigurationBuilder()
            .AddInMemoryCollection(values.ToDictionary(v => v.Key, v => (string?)v.Value))
            .Build());

    [Fact]
    public void Defaults_MatchPython()
    {
        var settings = Load();

        settings.A2aTransport.Should().Be("legacy");
        settings.A2aPublicBaseUrl.Should().BeEmpty();
        settings.A2aMaxActiveTasks.Should().Be(32);
        settings.A2aMaxActiveTasksPerCaller.Should().Be(8);
    }

    [Theory]
    [InlineData("a2a")]
    [InlineData("A2A")]
    [InlineData(" legacy ")]
    public void Transport_AcceptsTheTwoWires(string value) =>
        Load(("A2A_TRANSPORT", value)).A2aTransport.Should().Be(value.Trim().ToLowerInvariant());

    [Theory]
    [InlineData("grpc")]
    [InlineData("")]
    [InlineData("a2a1")]
    public void Transport_RejectsAnythingElse_AtStartup(string value)
    {
        // Including an empty value that was set: Python's field is a Literal, which rejects it the same way.
        var load = () => Load(("A2A_TRANSPORT", value));

        load.Should().Throw<InvalidOperationException>().WithMessage("*A2A_TRANSPORT*");
    }

    [Theory]
    [InlineData("A2A_MAX_ACTIVE_TASKS", "0")]
    [InlineData("A2A_MAX_ACTIVE_TASKS_PER_CALLER", "-3")]
    [InlineData("A2A_IDENTITY_TTL_SECONDS", "29")]
    [InlineData("A2A_IDENTITY_TTL_SECONDS", "901")]
    public void OutOfRangeNumbers_AreRejectedAtStartup_AsPythonsFieldsDo(string name, string value)
    {
        var load = () => Load((name, value));

        load.Should().Throw<InvalidOperationException>().WithMessage($"*{name}*");
    }

    [Fact]
    public void Identity_Defaults_MatchPython()
    {
        var settings = Load();

        settings.A2aIdentity.Should().Be("both");
        settings.A2aIdentityKey.Should().BeEmpty();
        settings.A2aIdentityTtlSeconds.Should().Be(300);
    }

    [Theory]
    [InlineData("headers")]
    [InlineData("TOKEN")]
    [InlineData(" both ")]
    public void Identity_AcceptsTheThreeModes(string value) =>
        Load(("A2A_IDENTITY", value)).A2aIdentity.Should().Be(value.Trim().ToLowerInvariant());

    [Theory]
    [InlineData("jwt")]
    [InlineData("")]
    public void Identity_RejectsAnythingElse_AtStartup(string value)
    {
        var load = () => Load(("A2A_IDENTITY", value));

        load.Should().Throw<InvalidOperationException>().WithMessage("*A2A_IDENTITY*");
    }
}
