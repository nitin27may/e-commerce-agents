using ECommerceAgents.Shared.Workflows;
using FluentAssertions;
using Microsoft.Agents.AI.Workflows;
using Microsoft.Agents.AI.Workflows.Checkpointing;
using System.Text.Json;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// The real return-and-replace workflow, paused on its policy gate and resumed by a
/// workflow object that never saw the original run — with a <see cref="ReturnDecision"/> as the answer.
/// </summary>
/// <remarks>
/// <c>MafCheckpointResumeSpikeTests</c> proved MAF can do this with a trivial graph and
/// primitive payloads. This proves it for the graph that actually handles refunds, whose
/// state is a custom type carrying lists and whose response is a record (the .NET counterpart
/// of Python's <c>test_return_decision_roundtrip.py</c> spike).
///
/// Deliberately builds a second <see cref="ReturnAndReplaceWorkflow"/> with its own
/// tools, because that is what a resume in a later process gets. Sharing the instance
/// would let executor fields carry state across the pause and prove nothing.
/// </remarks>
public sealed class ReturnReplaceCheckpointResumeTests
{
    [Fact]
    public async Task AHighValueReturn_ResumesInAFreshWorkflow_CreatesTheReturnOnlyAfterApproval()
    {
        var checkpoints = CheckpointManager.CreateInMemory();
        var sessionId = Guid.NewGuid().ToString();
        var before = new StubTools();

        var outcome = await NewWorkflow(before).RunAsync(
            new WorkflowState("c@example.com", "order-77") { OrderTotal = 803.46m, Reason = "damaged" },
            checkpoints: checkpoints,
            sessionId: sessionId);

        outcome.PendingRequestId.Should().NotBeNull("an $803.46 return is over the $500 threshold");
        outcome.LastCheckpointId.Should().NotBeNull(
            "the pause has to leave something durable behind or it cannot be resumed later");
        outcome.State.ReturnId.Should().BeNull("nothing is created before the approval");
        before.InitiateCalls.Should().Be(0);

        // A different workflow object, different tools — as if a later request, or a
        // restarted process, picked this up.
        var after = new StubTools();
        var resumed = await NewWorkflow(after).ResumeFromCheckpointAsync(
            checkpoints, sessionId, outcome.LastCheckpointId!, outcome.PendingRequestId!, approved: true);

        resumed.HitlApproved.Should().BeTrue();
        after.InitiateCalls.Should().Be(1, "the return is opened exactly once, after the decision");
        resumed.ReturnId.Should().Be("ret-777");
        resumed.Reason.Should().Be("damaged", "the full state the gate parked survives the restore");
        resumed.OrderTotal.Should().Be(803.46m);
        resumed.ReplacementProducts.Should().HaveCount(1);
        resumed.CompletedSteps.Should().Contain(["check_eligibility", "policy_gate", "initiate_return", "finalize"]);
    }

    [Fact]
    public async Task ADecisionRecord_RoundTripsThroughTheCheckpoint_AndItsEditsAreHonoured()
    {
        var checkpoints = CheckpointManager.CreateInMemory();
        var sessionId = Guid.NewGuid().ToString();
        var outcome = await NewWorkflow(new StubTools()).RunAsync(
            new WorkflowState("c@example.com", "order-80") { OrderTotal = 900m },
            checkpoints: checkpoints,
            sessionId: sessionId);

        var after = new StubTools();
        var resumed = await NewWorkflow(after).ResumeFromCheckpointAsync(
            checkpoints, sessionId, outcome.LastCheckpointId!, outcome.PendingRequestId!,
            new ReturnDecision("approve", "refund", 120m, "partial refund", "staff@example.com", "ap-9"));

        after.RefundMethods.Should().Equal("original_payment");
        after.Adjusted.Should().Equal(("ret-777", 120m));
        resumed.RefundAmount.Should().Be(120m);
        (resumed.Resolution, resumed.DecidedBy, resumed.ApprovalId).Should().Be(("refund", "staff@example.com", "ap-9"));
    }

    [Fact]
    public async Task ARejectedReturn_ResumesAndCreatesNothing()
    {
        var checkpoints = CheckpointManager.CreateInMemory();
        var sessionId = Guid.NewGuid().ToString();

        var outcome = await NewWorkflow(new StubTools()).RunAsync(
            new WorkflowState("c@example.com", "order-78") { OrderTotal = 900m },
            checkpoints: checkpoints,
            sessionId: sessionId);

        var after = new StubTools();
        var resumed = await NewWorkflow(after).ResumeFromCheckpointAsync(
            checkpoints, sessionId, outcome.LastCheckpointId!, outcome.PendingRequestId!, approved: false);

        resumed.HitlApproved.Should().BeFalse();
        resumed.Errors.Should().Contain(e => e.Contains("rejected", StringComparison.OrdinalIgnoreCase));
        resumed.CompletedSteps.Should().NotContain("finalize",
            "a rejected return must not be finalized");
        after.InitiateCalls.Should().Be(0, "a rejection leaves no return row and the order untouched");
        resumed.ReturnId.Should().BeNull();
    }

    [Fact]
    public async Task ResumingWithTheWrongRequestId_IsRefused()
    {
        var checkpoints = CheckpointManager.CreateInMemory();
        var sessionId = Guid.NewGuid().ToString();

        var outcome = await NewWorkflow(new StubTools()).RunAsync(
            new WorkflowState("c@example.com", "order-79") { OrderTotal = 900m },
            checkpoints: checkpoints,
            sessionId: sessionId);

        // Correlation matters: answering the wrong request would approve a refund the
        // reviewer never looked at.
        var act = async () => await NewWorkflow(new StubTools()).ResumeFromCheckpointAsync(
            checkpoints, sessionId, outcome.LastCheckpointId!, "not-the-right-request", approved: true);

        await act.Should().ThrowAsync<InvalidOperationException>()
            .WithMessage("*not-the-right-request*");
    }

    private static ReturnAndReplaceWorkflow NewWorkflow(StubTools tools) =>
        new(tools, hitlThreshold: 500m);

    private sealed class StubTools : IReturnReplaceTools
    {
        public int InitiateCalls;
        public List<string> RefundMethods { get; } = [];
        public List<(string ReturnId, decimal Amount)> Adjusted { get; } = [];

        public Task<ReturnEligibility> CheckReturnEligibilityAsync(string orderId, CancellationToken ct = default)
            => Task.FromResult(new ReturnEligibility(true));

        public Task<InitiateReturnResult> InitiateReturnAsync(string orderId, string reason, string refundMethod, CancellationToken ct = default)
        {
            Interlocked.Increment(ref InitiateCalls);
            RefundMethods.Add(refundMethod);
            return Task.FromResult(new InitiateReturnResult("ret-777", 803.46m));
        }

        public Task<AdjustReturnResult> AdjustReturnAmountAsync(string returnId, decimal amount, CancellationToken ct = default)
        {
            Adjusted.Add((returnId, amount));
            return Task.FromResult(new AdjustReturnResult(true));
        }

        public Task<IReadOnlyList<JsonElement>> SearchReplacementsAsync(decimal maxPrice, decimal minRating, int limit, CancellationToken ct = default)
            => Task.FromResult<IReadOnlyList<JsonElement>>(
                [JsonSerializer.SerializeToElement(new { id = "p1", name = "Replacement" })]);

        public Task<LoyaltyInfo?> GetLoyaltyTierAsync(CancellationToken ct = default)
            => Task.FromResult<LoyaltyInfo?>(new LoyaltyInfo("gold", 10m));
    }
}
