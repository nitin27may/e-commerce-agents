using ECommerceAgents.Orchestrator.Modes;
using ECommerceAgents.Shared.Orchestration;
using FluentAssertions;
using Xunit;

namespace ECommerceAgents.Orchestrator.Tests;

/// <summary>
/// The mode registry (#33 PR 5). Before it existed, <c>ChatRoutes</c> held a
/// single hardcoded agent call and the frontend's <c>mode</c> field was
/// discarded, so the mode switcher, graph panel and compare dialog all had
/// nothing to talk to.
/// </summary>
public sealed class ModeRegistryTests
{
    private sealed class FakeMode(string name, bool isGraph = false, string? mermaid = null) : IOrchestrationMode
    {
        public string Name => name;
        public string Label => $"{name} label";
        public string Description => $"{name} description";
        public ModeCapabilities Capabilities => new(IsGraph: isGraph);
        public string? GraphMermaid() => mermaid;
        public Task<ModeRunResult> RunAsync(string message, RunContext ctx, CancellationToken ct = default) =>
            Task.FromResult(new ModeRunResult($"ran {name}", [name], 1));
    }

    [Fact]
    public void Get_WithNoName_FallsBackToTheDefaultMode()
    {
        var registry = new ModeRegistry([new FakeMode("tool"), new FakeMode("workflow:x")]);

        registry.Get(null).Name.Should().Be("tool");
        registry.Get("").Name.Should().Be("tool");
    }

    [Fact]
    public void Get_IsCaseInsensitive_BecauseModeNamesArriveFromAClientQueryString()
    {
        var registry = new ModeRegistry([new FakeMode("workflow:pre-purchase")]);

        registry.Get("WORKFLOW:PRE-PURCHASE").Name.Should().Be("workflow:pre-purchase");
    }

    /// <summary>
    /// An unknown mode must name what is available. The alternative — running
    /// something else and saying nothing — is the exact silent downgrade this
    /// whole phase exists to remove.
    /// </summary>
    [Fact]
    public void Get_UnknownMode_ThrowsAndNamesTheAvailableOnes()
    {
        var registry = new ModeRegistry([new FakeMode("tool"), new FakeMode("workflow:pre-purchase")]);

        var act = () => registry.Get("group-chat");

        var ex = act.Should().Throw<UnknownModeException>().Which;
        ex.RequestedMode.Should().Be("group-chat");
        ex.AvailableModes.Should().Contain(["tool", "workflow:pre-purchase"]);
        ex.Message.Should().Contain("workflow:pre-purchase");
    }

    [Fact]
    public void Contains_DistinguishesRegisteredFromUnregistered()
    {
        var registry = new ModeRegistry([new FakeMode("tool")]);

        registry.Contains("tool").Should().BeTrue();
        registry.Contains("magentic").Should().BeFalse();
        registry.Contains(null).Should().BeFalse("a null mode means 'default', not 'registered'");
    }

    [Fact]
    public void Describe_EmitsTheSnakeCaseShapeTheClientParses()
    {
        var registry = new ModeRegistry([new FakeMode("workflow:x", isGraph: true, mermaid: "graph TD")]);

        var json = System.Text.Json.JsonSerializer.SerializeToElement(registry.Describe());
        var mode = json[0];

        mode.GetProperty("name").GetString().Should().Be("workflow:x");
        mode.GetProperty("label").GetString().Should().Be("workflow:x label");
        // The UI reads capabilities.is_graph to decide whether to show a graph
        // chip; camelCase here would silently render no chips at all.
        mode.GetProperty("capabilities").GetProperty("is_graph").GetBoolean().Should().BeTrue();
        mode.GetProperty("capabilities").GetProperty("supports_hitl").GetBoolean().Should().BeFalse();
    }
}
