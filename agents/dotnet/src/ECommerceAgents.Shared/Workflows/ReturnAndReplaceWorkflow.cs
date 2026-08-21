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
/// <summary>
/// What one run of the workflow produced, including whether it stopped on a human.
/// </summary>
/// <param name="State">The state as of the last output the run emitted.</param>
/// <param name="PendingRequestId">MAF's resume token when the run paused; null otherwise.</param>
/// <param name="SessionId">Keys the checkpoints this run wrote.</param>
/// <param name="LastCheckpointId">The checkpoint a later process resumes from.</param>
/// <remarks>
/// This record exists so a pause can leave the workflow at all. Previously the run
/// returned a bare <c>WorkflowState</c> and the RequestId surfaced only as a progress
/// report, so no caller could persist it — which is the root of .NET having a pause the
/// UI could see and nothing could resume.
/// </remarks>
public sealed record WorkflowRunOutcome(
    WorkflowState State,
    string? PendingRequestId,
    string SessionId,
    string? LastCheckpointId
);

public sealed class ReturnAndReplaceWorkflow
{
    /// <summary>Scope and key the paused state is parked under. Checkpointed with the run.</summary>
    private const string ScopeName = "return-replace";
    private const string StateKey = "state";

    private readonly IReturnReplaceTools _tools;
    private readonly decimal _threshold;
    private readonly Dictionary<string, (StreamingRun Run, ExternalRequest Request)> _pausedRuns = [];

    public ReturnAndReplaceWorkflow(IReturnReplaceTools tools, decimal hitlThreshold = 500m)
    {
        _tools = tools ?? throw new ArgumentNullException(nameof(tools));
        _threshold = hitlThreshold;
    }

    // ─────────────────────── execute ─────────────────────────

    /// <summary>
    /// The graph, built in exactly one place.
    /// </summary>
    /// <remarks>
    /// A run restored from a checkpoint must be given a graph identical to the one that
    /// wrote it — same executor ids, same port id, same edges. Two copies of this builder
    /// would drift, and drift here does not fail loudly: it surfaces as a restore that
    /// half-works.
    /// </remarks>
    private Workflow BuildWorkflow()
    {
        var check = new CheckEligibilityExecutor(_tools);
        var initiate = new InitiateReturnExecutor(_tools);
        var search = new SearchReplacementsExecutor(_tools);
        var port = RequestPort.Create<ReturnApprovalRequest, bool>("ReturnApproval");
        var gate = new GateDecisionExecutor(_threshold, port.Id);
        var resume = new HitlResumeExecutor();
        var discount = new ApplyDiscountExecutor(_tools);
        var finalize = new FinalizeExecutor();

        return new WorkflowBuilder(check)
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
    }

    public async Task<WorkflowState> ExecuteAsync(
        WorkflowState state,
        CancellationToken ct = default,
        IProgress<OrchestrationEvent>? events = null
    ) => (await RunAsync(state, ct, events)).State;

    /// <summary>
    /// Runs the graph, optionally checkpointing, and reports what it was waiting on if it
    /// paused — the <c>RequestId</c> and the checkpoint to resume from.
    /// </summary>
    /// <remarks>
    /// <see cref="ExecuteAsync"/> could only ever return the state, so a pause was
    /// unreachable by the caller: the RequestId existed but escaped only as a progress
    /// report. Nothing could persist it, which is why .NET had no resumable pause.
    /// </remarks>
    public async Task<WorkflowRunOutcome> RunAsync(
        WorkflowState state,
        CancellationToken ct = default,
        IProgress<OrchestrationEvent>? events = null,
        CheckpointManager? checkpoints = null,
        string? sessionId = null
    )
    {
        ArgumentNullException.ThrowIfNull(state);

        var workflow = BuildWorkflow();
        sessionId ??= Guid.NewGuid().ToString();

        var run = checkpoints is null
            ? await InProcessExecution.RunStreamingAsync(workflow, state, cancellationToken: ct)
            : await InProcessExecution.RunStreamingAsync(workflow, state, checkpoints, sessionId, ct);

        string? pendingRequestId = null;
        string? lastCheckpointId = null;
        ExternalRequest? pendingRequest = null;

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
                pendingRequest = requestInfo.Request;
                pendingRequestId = pendingRequest.RequestId;
                events?.Report(OrchestrationEvent.RequestInfo("hitl-gate", pendingRequestId));
            }

            // The checkpoint worth resuming from is the one taken on the superstep that
            // *ends* holding an outstanding request — it arrives after the
            // RequestInfoEvent, not with it, so the pause cannot be recorded from that
            // event alone.
            if (evt is SuperStepCompletedEvent step && step.CompletionInfo is { } info)
            {
                if (info.Checkpoint is not null)
                {
                    lastCheckpointId = info.Checkpoint.CheckpointId;
                }
                if (info.HasPendingRequests && pendingRequestId is not null)
                {
                    // Stop draining, but leave the run undisposed only in the
                    // no-checkpoint case; with checkpointing the durable record is
                    // enough and holding the object would just leak it.
                    if (checkpoints is null && pendingRequest is not null)
                    {
                        // No checkpoint to resume from, so the live run is the only
                        // record of the pause. Kept for the in-process path until every
                        // caller runs with checkpointing.
                        _pausedRuns[state.OrderId] = (run, pendingRequest);
                    }
                    return new WorkflowRunOutcome(finalState, pendingRequestId, sessionId, lastCheckpointId);
                }
            }
        }

        await run.DisposeAsync();
        return new WorkflowRunOutcome(finalState, pendingRequestId, sessionId, lastCheckpointId);
    }

    /// <summary>
    /// Resumes a paused run from a checkpoint, in a process that never saw the original.
    /// </summary>
    /// <remarks>
    /// The parity-correct counterpart to <see cref="ResumeAsync"/>: that one needs the
    /// live <c>StreamingRun</c> and so dies with the request that created it; this one
    /// needs only what is in storage. It rebuilds the graph, restores, waits for MAF to
    /// re-raise the request it was blocked on, and answers it.
    ///
    /// MAF re-surfaces the outstanding request on the restored stream rather than
    /// accepting a hand-built response, which is why the request is read back here
    /// instead of being reconstructed from <paramref name="requestId"/> — that argument
    /// is a correlation check, not the source of the response.
    /// </remarks>
    public async Task<WorkflowState> ResumeFromCheckpointAsync(
        CheckpointManager checkpoints,
        string sessionId,
        string checkpointId,
        string requestId,
        bool approved,
        CancellationToken ct = default,
        IProgress<OrchestrationEvent>? events = null
    )
    {
        ArgumentNullException.ThrowIfNull(checkpoints);

        var run = await InProcessExecution.ResumeStreamingAsync(
            BuildWorkflow(), new CheckpointInfo(sessionId, checkpointId), checkpoints, ct);

        ExternalRequest? pending = null;
        await foreach (var evt in run.WatchStreamAsync(ct))
        {
            if (evt is RequestInfoEvent info)
            {
                pending = info.Request;
                break;
            }
        }

        if (pending is null)
        {
            await run.DisposeAsync();
            throw new InvalidOperationException(
                $"Checkpoint {checkpointId} is not waiting on an approval — it may already have been resumed.");
        }

        if (!string.Equals(pending.RequestId, requestId, StringComparison.Ordinal))
        {
            await run.DisposeAsync();
            throw new InvalidOperationException(
                $"Checkpoint {checkpointId} is waiting on request {pending.RequestId}, not {requestId}.");
        }

        await run.SendResponseAsync(pending.CreateResponse(approved));

        WorkflowState? finalState = null;
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
            }

            if (evt is WorkflowOutputEvent { Data: WorkflowState s })
            {
                finalState = s;
            }
        }

        await run.DisposeAsync();
        return finalState
            ?? throw new InvalidOperationException("Resume produced no terminal state.");
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

                // Park the state in the workflow's own scope, because the port
                // carries a ReturnApprovalRequest in and a bool out — the state
                // itself does not cross it. Checkpointing captures this scope, so
                // it is what lets a rebuilt graph pick up where this one stopped;
                // a field on an executor would not survive the restore.
                await context.QueueStateUpdateAsync(StateKey, state, ScopeName, ct);

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
    /// </summary>
    /// <remarks>
    /// Reads the state back out of the workflow's scope rather than closing over
    /// it. The closure version worked only while the paused run stayed in memory;
    /// a graph rebuilt from a checkpoint gets freshly constructed executors, so a
    /// captured reference would point at an empty state and the workflow would
    /// finalize a return it had no record of opening. The scope is checkpointed;
    /// executor fields are not.
    /// </remarks>
    [SendsMessage(typeof(WorkflowState))]
    [YieldsOutput(typeof(WorkflowState))]
    private sealed class HitlResumeExecutor() : Executor<bool>("hitl-resume")
    {
        public override async ValueTask HandleAsync(bool approved, IWorkflowContext context, CancellationToken ct = default)
        {
            var state = await context.ReadStateAsync<WorkflowState>(StateKey, ScopeName, ct)
                ?? throw new InvalidOperationException(
                    $"No '{StateKey}' in scope '{ScopeName}' — the gate must park the state before the port pauses.");

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
