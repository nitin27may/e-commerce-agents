using ECommerceAgents.Shared.Workflows;
using FluentAssertions;
using System.Text.Json;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// <see cref="WorkflowState"/> has to survive JSON, because checkpoint-based resume
/// (#33) round-trips it through storage rather than holding the object in memory.
/// </summary>
/// <remarks>
/// This fails today, and the way it fails is the point: the three collections are
/// declared get-only (<c>public List&lt;string&gt; CompletedSteps { get; } = new();</c>),
/// so System.Text.Json constructs the state and then silently leaves them empty rather
/// than erroring. A resumed workflow would look entirely plausible while having lost the
/// steps it had already run and the return it had already opened.
/// </remarks>
public sealed class WorkflowStateRoundTripTests
{
    [Fact]
    public void WorkflowState_SurvivesAJsonRoundTrip()
    {
        var original = new WorkflowState("customer@example.com", "order-9")
        {
            OrderTotal = 803.46m,
            Reason = "damaged",
            ReturnEligible = true,
            ReturnId = "ret-abc",
            RefundAmount = 803.46m,
            HitlRequested = true,
        };
        original.CompletedSteps.Add("check_eligibility");
        original.CompletedSteps.Add("initiate_return");
        original.Errors.Add("none");
        original.ReplacementProducts.Add(JsonSerializer.SerializeToElement(new { id = "p1" }));

        var revived = JsonSerializer.Deserialize<WorkflowState>(JsonSerializer.Serialize(original))!;

        revived.UserEmail.Should().Be("customer@example.com");
        revived.OrderId.Should().Be("order-9");
        revived.ReturnId.Should().Be("ret-abc", "a resumed return must not re-open a second one");
        revived.RefundAmount.Should().Be(803.46m);
        revived.HitlRequested.Should().BeTrue();
        revived.CompletedSteps.Should().Equal("check_eligibility", "initiate_return");
        revived.Errors.Should().Equal("none");
        revived.ReplacementProducts.Should().HaveCount(1);
    }
}
