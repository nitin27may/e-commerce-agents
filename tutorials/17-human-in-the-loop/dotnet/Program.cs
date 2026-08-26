// MAF v1 — Chapter 17: Human-in-the-Loop (.NET)
//
// A workflow that pauses mid-run to ask a human approver whether a refund
// should go through, then resumes when the decision arrives. Demonstrates
// the canonical .NET HITL surface:
//
//   - RequestPort.Create<TRequest, TResponse>(id) — the pause/resume channel
//   - WorkflowBuilder(port).AddEdge(port, decision) — port feeds the decision
//     executor once a response comes back
//   - The caller sees a RequestInfoEvent on the stream, calls
//     run.SendResponseAsync(evt.Request.CreateResponse(approved)) and keeps
//     iterating the same StreamingRun until WorkflowOutputEvent arrives
//
// No LLM required — HITL is framework plumbing, not model behaviour.
//
// Run:
//   cd tutorials/17-human-in-the-loop/dotnet
//   dotnet run           # interactive: prompts for the approval decision
//   dotnet run -- y      # scripted: approves automatically
//   dotnet run -- n      # scripted: denies automatically

using Microsoft.Agents.AI.Workflows;

namespace MafV1.Ch17.Hitl;

/// <summary>
/// A refund awaiting a human approval decision. Doubles as both the value
/// that kicks the workflow off and the payload the request port hands to
/// the caller when it pauses — there's nothing extra to derive along the way.
/// </summary>
internal sealed record RefundRequest(string OrderId, double Amount);

/// <summary>
/// Receives the approve/deny decision routed back through the request port
/// and reports the outcome. The refund details are captured at construction
/// time — the same run already knows what it's asking approval for.
/// </summary>
[YieldsOutput(typeof(string))]
internal sealed class RefundDecisionExecutor(RefundRequest refund) : Executor<bool>("refund-decision")
{
    public override async ValueTask HandleAsync(
        bool approved,
        IWorkflowContext context,
        CancellationToken cancellationToken = default)
    {
        string message = approved
            ? $"refund approved for order {refund.OrderId}: ${refund.Amount:F2}"
            : $"refund denied for order {refund.OrderId}";
        await context.YieldOutputAsync(message, cancellationToken);
    }
}

public static class Program
{
    public static async Task<int> Main(string[] args)
    {
        RefundRequest refund = new("ord-482", 245.50);

        // Optional scripted mode: `dotnet run -- y` / `dotnet run -- n`
        // answers the approval automatically, which is useful for CI and for
        // readers who just want to see a deterministic pass.
        bool? scriptedApproval = args.Length > 0
            ? args[0].Equals("y", StringComparison.OrdinalIgnoreCase) || args[0].Equals("yes", StringComparison.OrdinalIgnoreCase)
            : null;

        Console.WriteLine("Chapter 17 — Human-in-the-Loop (refund approval)");
        Console.WriteLine();

        try
        {
            string outcome = await RunAsync(
                refund,
                approve: request =>
                {
                    bool approved = scriptedApproval ?? ReadApprovalFrom(request);
                    if (scriptedApproval is not null)
                    {
                        Console.WriteLine($"  -> sending scripted decision: {(approved ? "approve" : "deny")}");
                    }
                    return approved;
                });

            Console.WriteLine();
            Console.WriteLine(outcome);
            return 0;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"  [err]  {ex}");
            return 1;
        }
    }

    /// <summary>
    /// Builds the pause/resume workflow for one refund.
    /// </summary>
    /// <remarks>
    /// The request port is BOTH the starting executor — it emits a
    /// RequestInfoEvent as soon as the run is kicked off with the refund — and
    /// the upstream source of the decision executor, so the run pauses exactly
    /// once, then resolves.
    /// </remarks>
    internal static Workflow BuildWorkflow(RefundRequest refund)
    {
        RequestPort approvalPort = RequestPort.Create<RefundRequest, bool>("ApproveRefund");
        RefundDecisionExecutor decision = new(refund);

        return new WorkflowBuilder(approvalPort)
            .AddEdge(approvalPort, decision)
            .WithOutputFrom(decision)
            .Build();
    }

    /// <summary>
    /// Runs the workflow to completion, resolving the approval gate with
    /// <paramref name="approve"/>.
    /// </summary>
    /// <param name="approve">
    /// Called once, when the run pauses. Console prompt in the app; a lambda in
    /// the tests. Injecting the decision rather than reading Console.In is what
    /// makes this chapter testable — the alternative is a test that hangs
    /// waiting on stdin.
    /// </param>
    /// <returns>The decision executor's message.</returns>
    internal static async Task<string> RunAsync(
        RefundRequest refund,
        Func<RequestInfoEvent, bool> approve,
        CancellationToken cancellationToken = default)
    {
        Workflow workflow = BuildWorkflow(refund);

        await using StreamingRun run = await InProcessExecution.RunStreamingAsync(workflow, refund);

        // Single StreamingRun, single foreach. The pause is handled inline
        // with run.SendResponseAsync(...); the framework routes the response
        // to the decision executor. Contrast with Python where you make two
        // separate workflow.run(...) calls.
        await foreach (WorkflowEvent evt in run.WatchStreamAsync().WithCancellation(cancellationToken))
        {
            switch (evt)
            {
                case RequestInfoEvent request:
                    await run.SendResponseAsync(request.Request.CreateResponse(approve(request)));
                    break;

                case WorkflowOutputEvent output:
                    return output.Data?.ToString() ?? string.Empty;

                case ExecutorFailedEvent failed:
                    throw new InvalidOperationException(
                        $"executor '{failed.ExecutorId}' failed: {failed.Data}");

                case WorkflowErrorEvent error:
                    throw error.Exception ?? new InvalidOperationException("workflow failed");
            }
        }

        throw new InvalidOperationException("the workflow completed without producing an output");
    }

    private static bool ReadApprovalFrom(RequestInfoEvent evt)
    {
        string prompt = evt.Request.TryGetDataAs<RefundRequest>(out RefundRequest? refund) && refund is not null
            ? $"Approve refund of ${refund.Amount:F2} for order {refund.OrderId}? [y/n]: "
            : "Approve? [y/n]: ";
        Console.Write(prompt);

        string? line = Console.ReadLine()?.Trim();
        return string.Equals(line, "y", StringComparison.OrdinalIgnoreCase)
            || string.Equals(line, "yes", StringComparison.OrdinalIgnoreCase);
    }
}
