using Microsoft.Agents.AI.Workflows;

using ECommerceAgents.Shared.Orchestration;

namespace ECommerceAgents.Shared.Workflows;

/// <summary>
/// Sequential return-and-replace workflow with a human-in-the-loop
/// approval gate for high-value orders — .NET parity port of
/// <c>agents/python/workflows/return_replace.py</c>, now on a real MAF
/// <c>WorkflowBuilder</c> graph with a <see cref="RequestPort"/> pause/
/// resume gate (issue #17, piece 3 of 3).
/// </summary>
/// <remarks>
/// <para>
/// Step chain: check-eligibility → initiate-return → search-replacements
/// → hitl-gate → [ReturnApproval port, high value only] → apply-discount
/// → finalize.
/// </para>
/// <para>
/// Python's <c>ctx.request_info(...)</c> can pause a workflow from inside
/// any executor's own handler; MAF .NET has no equivalent ad-hoc call —
/// pausing requires a dedicated <see cref="RequestPort"/> graph node
/// (confirmed via the SDK's real API surface: <c>IWorkflowContext</c> has
/// no <c>RequestInfoAsync</c>-shaped member). So the conditional "pause
/// only above threshold" behavior Python expresses as one branch inside
/// <c>_HitlGateExecutor.run</c> is expressed here as <see cref="GateDecisionExecutor"/>
/// routing to one of two targets by explicit <c>targetId</c> — the
/// <see cref="RequestPort"/> for the high-value path, straight to
/// apply-discount for the low-value one — rather than a single executor
/// deciding whether to pause itself.
/// </para>
/// <para>
/// Python's two-call <c>execute()</c> / resume-via-<c>workflow.run(responses=...)</c>
/// contract maps to .NET's <see cref="InProcessExecution.RunStreamingAsync{T}"/>
/// + a single long-lived <see cref="StreamingRun"/> that both
/// <see cref="ExecuteAsync"/> and <see cref="ResumeAsync"/> share (verified
/// empirically before writing this: break out of the first
/// <c>WatchStreamAsync()</c> enumeration on <see cref="RequestInfoEvent"/>
/// without disposing the run, cache it, then later call
/// <c>SendResponseAsync</c> and open a fresh <c>WatchStreamAsync()</c> on
/// the same run — MAF resumes correctly from where it paused). The paused
/// <see cref="StreamingRun"/> is cached in <see cref="_pausedRuns"/>, keyed
/// by <see cref="WorkflowState.OrderId"/> — this workflow instance is
/// meant to be constructed once and reused across many executions/orders
/// (same contract the original hand-rolled version had), so per-order
/// keying lets multiple orders pause concurrently on one instance.
/// </para>
/// <para>
/// One deliberate improvement over Python here, not just a port: Python's
/// <c>@response_handler</c> only receives the <c>ReturnApprovalRequest</c>
/// snapshot it originally sent (a genuinely different, narrower payload
/// than the full <c>WorkflowState</c>), so its resumed state loses
/// <c>return_id</c>/<c>replacement_products</c>/<c>user_email</c> — its own
/// module comment calls this out as accepted lossy rehydration. .NET's
/// executors are plain constructed objects with full closure access, so
/// <see cref="HitlResumeExecutor"/> is constructed holding a reference to
/// the *same* <see cref="WorkflowState"/> instance threaded through the
/// rest of the chain — nothing is lost on resume.
/// </para>
/// </remarks>
public sealed class ReturnAndReplaceWorkflow
{
    private readonly IReturnReplaceTools _tools;
    private readonly decimal _threshold;
    private readonly Dictionary<string, (StreamingRun Run, ExternalRequest Request)> _pausedRuns = [];

    public ReturnAndReplaceWorkflow(IReturnReplaceTools tools, decimal hitlThreshold = 500m)
    {
        _tools = tools ?? throw new ArgumentNullException(nameof(tools));
        _threshold = hitlThreshold;
    }

    // ─────────────────────── execute ─────────────────────────

    public async Task<WorkflowState> ExecuteAsync(
        WorkflowState state,
        CancellationToken ct = default,
        IProgress<OrchestrationEvent>? events = null
    )
    {
        ArgumentNullException.ThrowIfNull(state);

        var check = new CheckEligibilityExecutor(_tools);
        var initiate = new InitiateReturnExecutor(_tools);
        var search = new SearchReplacementsExecutor(_tools);
        var port = RequestPort.Create<ReturnApprovalRequest, bool>("ReturnApproval");
        var gate = new GateDecisionExecutor(_threshold, port.Id);
        var resume = new HitlResumeExecutor(state);
        var discount = new ApplyDiscountExecutor(_tools);
        var finalize = new FinalizeExecutor();

        var workflow = new WorkflowBuilder(check)
            .AddEdge(check, initiate)
            .AddEdge(initiate, search)
            .AddEdge(search, gate)
            .AddEdge(gate, port)
            .AddEdge(gate, discount)
            .AddEdge(port, resume)
            .AddEdge(resume, discount)
            .AddEdge(discount, finalize)
            .WithOutputFrom(new ExecutorBinding[] { check, initiate, gate, resume, finalize })
            .Build();

        var run = await InProcessExecution.RunStreamingAsync(workflow, state, cancellationToken: ct);

        var finalState = state;
        await foreach (var evt in run.WatchStreamAsync(ct))
        {
            switch (evt)
            {
                case ExecutorInvokedEvent invoked:
                    events?.Report(OrchestrationEvent.NodeEnter(invoked.ExecutorId));
                    break;
                case ExecutorCompletedEvent completed:
                    events?.Report(OrchestrationEvent.NodeExit(completed.ExecutorId));
                    break;
                case ExecutorFailedEvent failed:
                    events?.Report(OrchestrationEvent.NodeError(
                        failed.ExecutorId,
                        failed.Data?.Message ?? "executor failed"
                    ));
                    break;
            }

            if (evt is WorkflowOutputEvent output && output.Data is WorkflowState s)
            {
                finalState = s;
            }
            if (evt is RequestInfoEvent requestInfo)
            {
                // The pause is a first-class outcome, not an absence of one —
                // the UI needs to know a human is now the blocker, which is
                // exactly what /runs renders an approval prompt from.
                events?.Report(OrchestrationEvent.RequestInfo("hitl-gate", requestInfo.Request.RequestId));

                // Pause: keep the run alive (do NOT dispose) and cache it —
                // ResumeAsync retrieves it by OrderId later, possibly in an
                // entirely separate request.
                _pausedRuns[state.OrderId] = (run, requestInfo.Request);
                return finalState;
            }
        }

        await run.DisposeAsync();
        return finalState;
    }

    // ─────────────────────── resume ──────────────────────────

    public async Task<WorkflowState> ResumeAsync(
        WorkflowState state,
        bool approved,
        CancellationToken ct = default
    )
    {
        ArgumentNullException.ThrowIfNull(state);
        if (!_pausedRuns.Remove(state.OrderId, out var paused))
        {
            throw new InvalidOperationException(
                "Workflow is not waiting on a HITL response; call ExecuteAsync first."
            );
        }

        await paused.Run.SendResponseAsync(paused.Request.CreateResponse(approved));

        var finalState = state;
        await foreach (var evt in paused.Run.WatchStreamAsync(ct))
        {
            if (evt is WorkflowOutputEvent output && output.Data is WorkflowState s)
            {
                finalState = s;
            }
        }

        await paused.Run.DisposeAsync();
        return finalState;
    }

    public ReturnApprovalRequest BuildApprovalRequest(WorkflowState state) =>
        new(state.OrderId, state.OrderTotal, state.RefundAmount, state.ReplacementProducts.Count);

    // ─────────────────────── executors ───────────────────────

    [SendsMessage(typeof(WorkflowState))]
    [YieldsOutput(typeof(WorkflowState))]
    private sealed class CheckEligibilityExecutor(IReturnReplaceTools tools) : Executor<WorkflowState>("check-eligibility")
    {
        public override async ValueTask HandleAsync(WorkflowState state, IWorkflowContext context, CancellationToken ct = default)
        {
            ReturnEligibility result;
            try
            {
                result = await tools.CheckReturnEligibilityAsync(state.OrderId, ct);
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                state.Errors.Add($"check_eligibility: {ex.Message}");
                await context.YieldOutputAsync(state, ct);
                return;
            }

            state.ReturnEligible = result.Eligible;
            state.CompletedSteps.Add("check_eligibility");
            if (!result.Eligible)
            {
                state.Errors.Add(result.Reason ?? "Not eligible for return");
                await context.YieldOutputAsync(state, ct);
                return;
            }
            await context.SendMessageAsync(state, ct);
        }
    }

    [SendsMessage(typeof(WorkflowState))]
    [YieldsOutput(typeof(WorkflowState))]
    private sealed class InitiateReturnExecutor(IReturnReplaceTools tools) : Executor<WorkflowState>("initiate-return")
    {
        public override async ValueTask HandleAsync(WorkflowState state, IWorkflowContext context, CancellationToken ct = default)
        {
            InitiateReturnResult result;
            try
            {
                var reason = string.IsNullOrWhiteSpace(state.Reason) ? "Customer requested replacement" : state.Reason;
                result = await tools.InitiateReturnAsync(state.OrderId, reason, "store_credit", ct);
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                state.Errors.Add($"initiate_return: {ex.Message}");
                await context.YieldOutputAsync(state, ct);
                return;
            }

            if (!string.IsNullOrEmpty(result.Error))
            {
                state.Errors.Add($"initiate_return: {result.Error}");
                await context.YieldOutputAsync(state, ct);
                return;
            }

            state.ReturnId = result.ReturnId;
            state.RefundAmount = result.RefundAmount;
            state.CompletedSteps.Add("initiate_return");
            await context.SendMessageAsync(state, ct);
        }
    }

    [SendsMessage(typeof(WorkflowState))]
    private sealed class SearchReplacementsExecutor(IReturnReplaceTools tools) : Executor<WorkflowState>("search-replacements")
    {
        public override async ValueTask HandleAsync(WorkflowState state, IWorkflowContext context, CancellationToken ct = default)
        {
            try
            {
                var results = await tools.SearchReplacementsAsync(maxPrice: state.RefundAmount * 1.2m, minRating: 4.0m, limit: 5, ct);
                state.ReplacementProducts.AddRange(results);
                state.CompletedSteps.Add("search_replacements");
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                state.Errors.Add($"search_replacements: {ex.Message}");
            }
            await context.SendMessageAsync(state, ct);
        }
    }

    /// <summary>
    /// Decides whether this return needs approval — the .NET analog of the
    /// branch inside Python's <c>_HitlGateExecutor.run</c>. Routes to
    /// <paramref name="approvalPortId"/> (pausing the workflow) above
    /// <paramref name="threshold"/>, or straight past it otherwise —
    /// explicit <c>targetId</c> routing since the two outbound message
    /// types differ (<see cref="ReturnApprovalRequest"/> vs.
    /// <see cref="WorkflowState"/>), unlike a same-type conditional edge.
    /// </summary>
    [SendsMessage(typeof(ReturnApprovalRequest))]
    [SendsMessage(typeof(WorkflowState))]
    [YieldsOutput(typeof(WorkflowState))]
    private sealed class GateDecisionExecutor(decimal threshold, string approvalPortId) : Executor<WorkflowState>("hitl-gate")
    {
        public override async ValueTask HandleAsync(WorkflowState state, IWorkflowContext context, CancellationToken ct = default)
        {
            state.CompletedSteps.Add("hitl_gate");
            if (state.OrderTotal > threshold)
            {
                state.HitlRequested = true;
                // Snapshot so a caller observing the stream sees the pause
                // state before the RequestInfoEvent actually pauses the run.
                await context.YieldOutputAsync(state, ct);
                var request = new ReturnApprovalRequest(state.OrderId, state.OrderTotal, state.RefundAmount, state.ReplacementProducts.Count);
                await context.SendMessageAsync(request, approvalPortId, ct);
                return;
            }

            state.HitlApproved = true;
            await context.SendMessageAsync(state, ct);
        }
    }

    /// <summary>
    /// Fed by the <see cref="RequestPort"/> once a response arrives.
    /// Constructed holding a reference to the same <see cref="WorkflowState"/>
    /// threaded through the rest of the chain (see the class-level remarks
    /// for why this improves on Python's lossy rehydration here).
    /// </summary>
    [SendsMessage(typeof(WorkflowState))]
    [YieldsOutput(typeof(WorkflowState))]
    private sealed class HitlResumeExecutor(WorkflowState state) : Executor<bool>("hitl-resume")
    {
        public override async ValueTask HandleAsync(bool approved, IWorkflowContext context, CancellationToken ct = default)
        {
            state.HitlApproved = approved;
            if (!approved)
            {
                state.Errors.Add("hitl_gate: return rejected by reviewer");
                await context.YieldOutputAsync(state, ct);
                return;
            }
            await context.SendMessageAsync(state, ct);
        }
    }

    [SendsMessage(typeof(WorkflowState))]
    private sealed class ApplyDiscountExecutor(IReturnReplaceTools tools) : Executor<WorkflowState>("apply-discount")
    {
        public override async ValueTask HandleAsync(WorkflowState state, IWorkflowContext context, CancellationToken ct = default)
        {
            try
            {
                var info = await tools.GetLoyaltyTierAsync(ct);
                if (info is not null && info.DiscountPct > 0m)
                {
                    state.AppliedDiscount = new WorkflowState.LoyaltyDiscount(info.Tier, info.DiscountPct);
                }
                state.CompletedSteps.Add("apply_discount");
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                state.Errors.Add($"apply_discount: {ex.Message}");
            }
            await context.SendMessageAsync(state, ct);
        }
    }

    [YieldsOutput(typeof(WorkflowState))]
    private sealed class FinalizeExecutor() : Executor<WorkflowState>("finalize")
    {
        public override async ValueTask HandleAsync(WorkflowState state, IWorkflowContext context, CancellationToken ct = default)
        {
            state.CompletedSteps.Add("finalize");
            await context.YieldOutputAsync(state, ct);
        }
    }
}
