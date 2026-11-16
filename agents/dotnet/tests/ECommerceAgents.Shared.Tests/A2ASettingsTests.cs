using ECommerceAgents.Shared.Configuration;
using FluentAssertions;
using Microsoft.Extensions.Configuration;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>The A2A settings: the same names, defaults and validation as Python's <c>shared/config.py</c>.</summary>
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

    [Fact]
    public void ActiveTaskCaps_AreFlooredAtOne()
    {
        var settings = Load(("A2A_MAX_ACTIVE_TASKS", "0"), ("A2A_MAX_ACTIVE_TASKS_PER_CALLER", "-3"));

        settings.A2aMaxActiveTasks.Should().Be(1);
        settings.A2aMaxActiveTasksPerCaller.Should().Be(1);
    }
}
