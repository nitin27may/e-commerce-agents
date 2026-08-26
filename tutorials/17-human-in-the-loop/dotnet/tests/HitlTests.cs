// MAF v1 — Chapter 17 tests (Human-in-the-Loop)
//
// HITL is framework plumbing, not model behaviour, so there is no LLM here and
// nothing to fake — these are ordinary deterministic tests of a pause/resume
// protocol. That also makes them the cheapest tests in the series to keep
// green, which is why the chapter having none was the odd one out.
//
// The .NET shape differs from Python's in a way readers get wrong: it is ONE
// StreamingRun and ONE foreach. The pause is resolved inline with
// run.SendResponseAsync(...) while still iterating the same stream, where
// Python makes two separate workflow.run(...) calls. Several assertions below
// exist to pin that down.

using FluentAssertions;
using Microsoft.Agents.AI.Workflows;
using Xunit;

namespace MafV1.Ch17.Hitl.Tests;

public sealed class HitlTests
{
    private static readonly RefundRequest Refund = new("ord-482", 245.50);

    [Fact]
    public async Task Approving_Yields_An_Approved_Message_With_The_Amount()
    {
        string outcome = await Program.RunAsync(Refund, approve: _ => true);

        outcome.Should().Be("refund approved for order ord-482: $245.50");
    }

    [Fact]
    public async Task Denying_Yields_A_Denied_Message()
    {
        string outcome = await Program.RunAsync(Refund, approve: _ => false);

        outcome.Should().Be("refund denied for order ord-482");
        outcome.Should().NotContain("245.50", "a denied refund must not quote an amount as if it were paid");
    }

    [Fact]
    public async Task The_Run_Pauses_Exactly_Once()
    {
        // The topology claim: the request port is both the entry point and the
        // decision executor's upstream, so there is one gate, not two. If a
        // future edit adds a second edge this fires.
        int prompts = 0;

        await Program.RunAsync(Refund, approve: _ => { prompts++; return true; });

        prompts.Should().Be(1);
    }

    [Fact]
    public async Task The_Pause_Carries_The_Refund_Being_Decided()
    {
        // An approval gate that does not tell the approver what they are
        // approving is a rubber stamp. The payload has to survive the port.
        RefundRequest? seen = null;

        await Program.RunAsync(Refund, approve: request =>
        {
            request.Request.TryGetDataAs(out RefundRequest? data);
            seen = data;
            return true;
        });

        seen.Should().NotBeNull();
        seen!.OrderId.Should().Be("ord-482");
        seen.Amount.Should().Be(245.50);
    }

    [Fact]
    public async Task Nothing_Downstream_Runs_Before_The_Decision_Arrives()
    {
        // The actual point of HITL. If the decision executor could run ahead of
        // the human, the gate would be decorative — so assert the ordering
        // rather than just the final answer.
        var order = new List<string>();

        Workflow workflow = Program.BuildWorkflow(Refund);
        await using StreamingRun run = await InProcessExecution.RunStreamingAsync(workflow, Refund);

        await foreach (WorkflowEvent evt in run.WatchStreamAsync())
        {
            switch (evt)
            {
                case RequestInfoEvent request:
                    order.Add("paused");
                    await run.SendResponseAsync(request.Request.CreateResponse(true));
                    break;

                case ExecutorInvokedEvent invoked when invoked.ExecutorId == "refund-decision":
                    order.Add("decision-ran");
                    break;

                case WorkflowOutputEvent:
                    order.Add("output");
                    break;
            }
        }

        order.Should().ContainInOrder("paused", "decision-ran", "output");
    }

    [Fact]
    public async Task A_Different_Refund_Produces_A_Different_Message()
    {
        // Guards against the executor closing over the wrong instance — it
        // captures the refund at construction time, which is easy to get wrong
        // once more than one refund is in flight.
        string outcome = await Program.RunAsync(new RefundRequest("ord-999", 12.00), approve: _ => true);

        outcome.Should().Be("refund approved for order ord-999: $12.00");
    }

    [Fact]
    public void The_Workflow_Builds_Before_Anything_Is_Run()
    {
        Program.BuildWorkflow(Refund).Should().NotBeNull();
    }
}
