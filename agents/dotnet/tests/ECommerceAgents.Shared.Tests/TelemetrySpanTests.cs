using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Telemetry;
using FluentAssertions;
using System.Diagnostics;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// The GenAI span convention that decides whether .NET runs show up in Aspire
/// at all (#19).
/// </summary>
/// <remarks>
/// There were no telemetry tests on this stack, which is why the span names
/// could drift from Python's without anyone noticing. The names are not
/// cosmetic: Aspire's GenAI view selects on <c>invoke_agent</c> plus
/// <c>gen_ai.operation.name</c>, so <c>agent.run</c> + <c>chat</c> renders
/// nowhere in it. Python chose <c>invoke_agent</c> deliberately and documents
/// why; .NET had simply never matched it.
/// </remarks>
public sealed class TelemetrySpanTests : IDisposable
{
    private readonly ActivityListener _listener;
    private readonly List<Activity> _started = new();

    public TelemetrySpanTests()
    {
        // Without a listener that samples AllData, StartActivity returns null
        // and every assertion below would vacuously pass on a null span.
        _listener = new ActivityListener
        {
            ShouldListenTo = src => src.Name == TelemetrySetup.SourceName,
            Sample = (ref ActivityCreationOptions<ActivityContext> _) => ActivitySamplingResult.AllDataAndRecorded,
            ActivityStarted = _started.Add,
        };
        ActivitySource.AddActivityListener(_listener);
    }

    public void Dispose() => _listener.Dispose();

    [Fact]
    public void AgentRunSpan_UsesTheGenAiAgentConvention()
    {
        using var activity = TelemetrySetup.AgentRunSpan("product-discovery", "gpt-4.1");

        activity.Should().NotBeNull("the test listener must be sampling, or this test proves nothing");
        activity!.DisplayName.Should().Be("invoke_agent product-discovery");
        activity.GetTagItem("gen_ai.operation.name").Should().Be("invoke_agent");
        activity.GetTagItem("gen_ai.system").Should().Be("openai");
        activity.GetTagItem("gen_ai.request.model").Should().Be("gpt-4.1");
        activity.Kind.Should().Be(ActivityKind.Internal);
    }

    [Fact]
    public void A2ACallSpan_IsAClientSpanUsingTheSameConvention()
    {
        using var activity = TelemetrySetup.A2ACallSpan("orchestrator", "order-management", "http://om:8082");

        activity.Should().NotBeNull();
        activity!.DisplayName.Should().Be("invoke_agent order-management");
        activity.GetTagItem("gen_ai.operation.name").Should().Be("invoke_agent");
        activity.GetTagItem("gen_ai.agent.name").Should().Be("order-management");
        // Kept alongside the GenAI tags so peer-service topology still renders.
        activity.GetTagItem("peer.service").Should().Be("order-management");
        activity.Kind.Should().Be(ActivityKind.Client);
    }

    [Fact]
    public void Spans_CarryTheConversationSoAspireCanGroupThem()
    {
        using var scope = RequestContext.Scope("alice@example.com", "customer", "11111111-1111-1111-1111-111111111111", []);

        using var activity = TelemetrySetup.AgentRunSpan("orchestrator", "gpt-4.1");

        activity.Should().NotBeNull();
        activity!.GetTagItem("enduser.id").Should().Be("alice@example.com");
        activity.GetTagItem("enduser.role").Should().Be("customer");
        activity.GetTagItem("session.id").Should().Be("11111111-1111-1111-1111-111111111111");
        // The attribute Aspire actually groups a conversation's LLM calls by.
        activity.GetTagItem("gen_ai.conversation.id").Should().Be("11111111-1111-1111-1111-111111111111");
        activity.GetTagItem("gen_ai.agent.name").Should().Be("orchestrator");
    }

    [Fact]
    public void EnrichmentOmitsEmptyValuesRatherThanTaggingBlanks()
    {
        using var scope = RequestContext.Scope("", "", "", []);

        using var activity = TelemetrySetup.AgentRunSpan("orchestrator", "gpt-4.1");

        activity.Should().NotBeNull();
        activity!.GetTagItem("enduser.id").Should().BeNull();
        activity.GetTagItem("session.id").Should().BeNull();
        activity.GetTagItem("gen_ai.conversation.id").Should().BeNull();
    }
}
