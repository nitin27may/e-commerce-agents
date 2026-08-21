using ECommerceAgents.Shared.Workflows;
using FluentAssertions;
using Microsoft.Agents.AI.Workflows;
using Microsoft.Agents.AI.Workflows.Checkpointing;
using System.Text.Json;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// The real return-and-replace workflow, paused on its HITL gate and resumed by a
/// workflow object that never saw the original run.
/// </summary>
/// <remarks>
/// <c>MafCheckpointResumeSpikeTests</c> proved MAF can do this with a trivial graph and
/// primitive payloads. This proves it for the graph that actually handles refunds, whose
/// state is a custom type carrying lists — the case where a silent serialization gap
/// would let a resumed run finalize a return it has no record of opening.
///
/// Deliberately builds a second <see cref="ReturnAndReplaceWorkflow"/> with its own
/// tools, because that is what a resume in a later process gets. Sharing the instance
/// would let executor fields carry state across the pause and prove nothing.
/// </remarks>
public sealed class ReturnReplaceCheckpointResumeTests
{
    [Fact]
    public async Task AHighValueReturn_ResumesInAFreshWorkflow_AndKeepsEverythingItLearnedBeforeThePause()
    {
        var checkpoints = CheckpointManager.CreateInMemory();
        var sessionId = Guid.NewGuid().ToString();

        var outcome = await NewWorkflow().RunAsync(
            new WorkflowState("c@example.com", "order-77") { OrderTotal = 803.46m, Reason = "damaged" },
            checkpoints: checkpoints,
            sessionId: sessionId);

        outcome.PendingRequestId.Should().NotBeNull("an $803.46 return is over the $500 threshold");
        outcome.LastCheckpointId.Should().NotBeNull(
            "the pause has to leave something durable behind or it cannot be resumed later");
        outcome.State.ReturnId.Should().Be("ret-777");

        // A different workflow object, different tools — as if a later request, or a
        // restarted process, picked this up.
        var resumed = await NewWorkflow().ResumeFromCheckpointAsync(
            checkpoints, sessionId, outcome.LastCheckpointId!, outcome.PendingRequestId!, approved: true);

        resumed.HitlApproved.Should().BeTrue();
        resumed.ReturnId.Should().Be("ret-777",
            "the return opened before the pause must survive, or the resumed run opens a second one");
        resumed.RefundAmount.Should().Be(803.46m);
        resumed.ReplacementProducts.Should().HaveCount(1, "work done before the pause is not redone");
        resumed.CompletedSteps.Should().Contain(["check_eligibility", "initiate_return", "hitl_gate", "finalize"]);
    }

    [Fact]
    public async Task ARejectedReturn_ResumesAndStopsBeforeFinalize()
    {
        var checkpoints = CheckpointManager.CreateInMemory();
        var sessionId = Guid.NewGuid().ToString();

        var outcome = await NewWorkflow().RunAsync(
            new WorkflowState("c@example.com", "order-78") { OrderTotal = 900m },
            checkpoints: checkpoints,
            sessionId: sessionId);

        var resumed = await NewWorkflow().ResumeFromCheckpointAsync(
            checkpoints, sessionId, outcome.LastCheckpointId!, outcome.PendingRequestId!, approved: false);

        resumed.HitlApproved.Should().BeFalse();
        resumed.Errors.Should().Contain(e => e.Contains("rejected", StringComparison.OrdinalIgnoreCase));
        resumed.CompletedSteps.Should().NotContain("finalize",
            "a rejected return must not be finalized");
    }

    [Fact]
    public async Task ResumingWithTheWrongRequestId_IsRefused()
    {
        var checkpoints = CheckpointManager.CreateInMemory();
        var sessionId = Guid.NewGuid().ToString();

        var outcome = await NewWorkflow().RunAsync(
            new WorkflowState("c@example.com", "order-79") { OrderTotal = 900m },
            checkpoints: checkpoints,
            sessionId: sessionId);

        // Correlation matters: answering the wrong request would approve a refund the
        // reviewer never looked at.
        var act = async () => await NewWorkflow().ResumeFromCheckpointAsync(
            checkpoints, sessionId, outcome.LastCheckpointId!, "not-the-right-request", approved: true);

        await act.Should().ThrowAsync<InvalidOperationException>()
            .WithMessage("*not-the-right-request*");
    }

    private static ReturnAndReplaceWorkflow NewWorkflow() =>
        new(new StubTools(), hitlThreshold: 500m);

    private sealed class StubTools : IReturnReplaceTools
    {
        public Task<ReturnEligibility> CheckReturnEligibilityAsync(string orderId, CancellationToken ct = default)
            => Task.FromResult(new ReturnEligibility(true));

        public Task<InitiateReturnResult> InitiateReturnAsync(string orderId, string reason, string refundMethod, CancellationToken ct = default)
            => Task.FromResult(new InitiateReturnResult("ret-777", 803.46m));

        public Task<IReadOnlyList<JsonElement>> SearchReplacementsAsync(decimal maxPrice, decimal minRating, int limit, CancellationToken ct = default)
            => Task.FromResult<IReadOnlyList<JsonElement>>(
                [JsonSerializer.SerializeToElement(new { id = "p1", name = "Replacement" })]);

        public Task<LoyaltyInfo?> GetLoyaltyTierAsync(CancellationToken ct = default)
            => Task.FromResult<LoyaltyInfo?>(new LoyaltyInfo("gold", 10m));
    }
}
