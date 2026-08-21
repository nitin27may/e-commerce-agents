using Microsoft.Agents.AI.Workflows;
using ECommerceAgents.Orchestrator.Modes;
using ECommerceAgents.Shared.Orchestration;
using ECommerceAgents.Shared.Configuration;
using FluentAssertions;
using System.Text.RegularExpressions;
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

    // ─────────────── graph ↔ executor id drift (#33 PR 6) ───────────────

    /// <summary>
    /// Every node in a mode's Mermaid must use the underscored form of a real
    /// executor id.
    /// </summary>
    /// <remarks>
    /// The UI lights up a diagram node by matching a live event's node_id to a
    /// node id in the graph, so an id that doesn't correspond to an executor
    /// is a node that can never animate — and nothing fails, it just sits
    /// dark. This shipped in PR 5a: the pre-purchase diagram said "merge"
    /// while the executor is "merge-and-ship", found only by reading the
    /// frames off the wire.
    /// </remarks>
    [Theory]
    [InlineData("workflow:pre-purchase", new[] { "fan-out", "reviews", "stock", "price-history", "merge-and-ship", "synthesis" })]
    [InlineData("workflow:return-replace", new[] { "check-eligibility", "initiate-return", "search-replacements", "hitl-gate", "hitl-resume", "apply-discount", "finalize" })]
    public void EveryGraphNodeId_MatchesARealExecutorId(string modeName, string[] executorIds)
    {
        var mermaid = modeName == "workflow:pre-purchase"
            ? new PrePurchaseMode(null!).GraphMermaid()
            : new ReturnReplaceMode(null!, new AgentSettings(), CheckpointManager.CreateInMemory()).GraphMermaid();

        mermaid.Should().NotBeNull();

        var expected = executorIds.Select(OrchestrationEvent.ToNodeId).ToHashSet();

        // Strip edge labels (|like this|) and node labels ([Like this],
        // {Like this}) first — both contain prose that would otherwise be
        // mistaken for ids.
        var skeleton = Regex.Replace(mermaid!, @"\|[^|]*\||\[[^\]]*\]|\{[^}]*\}", " ");

        var found = Regex.Matches(skeleton, @"[a-z_]+")
            .Select(m => m.Value)
            .Where(v => v is not ("graph" or "td"))
            .ToHashSet();

        var unknown = found.Except(expected).ToList();
        unknown.Should().BeEmpty(
            $"every node in {modeName}'s graph must correspond to an executor; " +
            $"known ids are {string.Join(", ", expected)}"
        );
    }
}
