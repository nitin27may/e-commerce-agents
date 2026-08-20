using ECommerceAgents.Shared.Context;
using FluentAssertions;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// <see cref="RequestContext.CurrentSteps"/> / <see cref="RequestContext.RecordStep"/>
/// — the agentic-timeline capture list (issue #16), the .NET analog of
/// Python's <c>current_steps</c> ContextVar.
/// </summary>
public sealed class RequestContextStepsTests
{
    [Fact]
    public void RecordStep_OutsideAnyScope_IsANoOp()
    {
        // No RequestContext.Scope open — matches Python's ContextVar default
        // (None outside a request that opted into capture).
        var act = () => RequestContext.RecordStep(new ExecutionStep("SearchProducts", null, null, "success", 5));
        act.Should().NotThrow();
    }

    [Fact]
    public void Scope_StartsWithAnEmptyStepList()
    {
        using var scope = RequestContext.Scope("u@example.com", "customer", "sess-1");
        RequestContext.CurrentSteps.Should().BeEmpty();
    }

    [Fact]
    public void RecordStep_AppendsInCallOrder()
    {
        using var scope = RequestContext.Scope("u@example.com", "customer", "sess-1");

        RequestContext.RecordStep(new ExecutionStep("SearchProducts", null, null, "success", 5));
        RequestContext.RecordStep(new ExecutionStep("GetProductDetails", null, null, "success", 3));

        RequestContext.CurrentSteps.Select(s => s.ToolName).Should().Equal("SearchProducts", "GetProductDetails");
    }

    [Fact]
    public void NestedScope_GetsItsOwnStepList_AndOuterScopeIsRestoredOnDispose()
    {
        using var outer = RequestContext.Scope("outer@example.com", "customer", "sess-outer");
        RequestContext.RecordStep(new ExecutionStep("OuterTool", null, null, "success", 1));

        using (var inner = RequestContext.Scope("inner@example.com", "customer", "sess-inner"))
        {
            RequestContext.CurrentSteps.Should().BeEmpty();
            RequestContext.RecordStep(new ExecutionStep("InnerTool", null, null, "success", 1));
            RequestContext.CurrentSteps.Select(s => s.ToolName).Should().Equal("InnerTool");
        }

        RequestContext.CurrentSteps.Select(s => s.ToolName).Should().Equal("OuterTool");
    }
}
