using Microsoft.Agents.AI.Workflows;

using ECommerceAgents.Shared.Orchestration;
using ECommerceAgents.Shared.Policy;

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
/// Step chain: check-eligibility → policy-gate → [ReturnApproval port →
/// policy-resume, unless the policy says <c>auto</c>] → initiate-return →
/// search-replacements → apply-discount → finalize.
/// </para>
/// <para>
/// Nothing is created before the gate: the <c>returns</c> row, the label and the order's
/// <c>returned</c> status all come from <c>initiate-return</c>, which runs only once the policy
/// has said <c>auto</c> or a reviewer has approved. A rejected (or expired) request leaves no
/// return row and the order untouched. The gate asks the approval policy engine
/// (<see cref="IReturnPolicy"/>, kind <c>return_approval</c>); <c>hitlThreshold</c> is only the
/// fallback for when the engine cannot decide. The port answers a <see cref="ReturnDecision"/>
/// (decision, resolution, amount, note, decided_by, approval_id) — the same shape as Python's —
/// and an approving reviewer's edited resolution and amount are honoured by <c>initiate-return</c>.
/// </para>
/// <para>
/// Python's <c>ctx.request_info(...)</c> can pause a workflow from inside
/// any executor's own handler; MAF .NET has no equivalent ad-hoc call —
/// pausing requires a dedicated <see cref="RequestPort"/> graph node
/// (confirmed via the SDK's real API surface: <c>IWorkflowContext</c> has
/// <c>RequestInfoAsync</c>-shaped member). So the conditional "pause
/// unless the policy says auto" behavior Python expresses as one branch inside
/// <c>_PolicyGateExecutor.run</c> is expressed here as <see cref="GateDecisionExecutor"/>
/// routing to one of two targets by explicit <c>targetId</c> — the
/// <see cref="RequestPort"/> when a person must decide, straight to
/// initiate-return for an <c>auto</c> route — rather than a single executor
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
/// The full <see cref="WorkflowState"/> survives the pause on both stacks: Python carries it in
/// the approval request, .NET parks it in the workflow's checkpointed scope (the port carries a
/// request in and a decision out, so the state itself does not cross it) and
/// <see cref="PolicyResumeExecutor"/> reads it back.
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
    private readonly IReturnPolicy? _policy;
    private readonly Dictionary<string, (StreamingRun Run, ExternalRequest Request)> _pausedRuns = [];

    public ReturnAndReplaceWorkflow(IReturnReplaceTools tools, decimal hitlThreshold = 500m, IReturnPolicy? policy = null)
    {
        _tools = tools ?? throw new ArgumentNullException(nameof(tools));
        _threshold = hitlThreshold;
        _policy = policy;
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
        var port = RequestPort.Create<ReturnApprovalRequest, ReturnDecision>("ReturnApproval");
        var gate = new GateDecisionExecutor(_threshold, _policy, port.Id);
        var resume = new PolicyResumeExecutor();
        var initiate = new InitiateReturnExecutor(_tools);
        var search = new SearchReplacementsExecutor(_tools);
        var discount = new ApplyDiscountExecutor(_tools);
        var finalize = new FinalizeExecutor();

        return new WorkflowBuilder(check)
            .AddEdge(check, gate)
            .AddEdge(gate, port)
            .AddEdge(gate, initiate)
            .AddEdge(port, resume)
            .AddEdge(resume, initiate)
            .AddEdge(initiate, search)
            .AddEdge(search, discount)
            .AddEdge(discount, finalize)
            .WithOutputFrom(new ExecutorBinding[] { check, gate, resume, initiate, finalize })
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
                events?.Report(OrchestrationEvent.RequestInfo("policy-gate", pendingRequestId));
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
    public Task<WorkflowState> ResumeFromCheckpointAsync(
        CheckpointManager checkpoints,
        string sessionId,
        string checkpointId,
        string requestId,
        bool approved,
        CancellationToken ct = default,
        IProgress<OrchestrationEvent>? events = null
    ) => ResumeFromCheckpointAsync(
        checkpoints, sessionId, checkpointId, requestId, ReturnDecision.FromApproved(approved), ct, events);

    /// <summary>As above, answering with a full <see cref="ReturnDecision"/> (resolution, edited amount, note).</summary>
    public async Task<WorkflowState> ResumeFromCheckpointAsync(
        CheckpointManager checkpoints,
        string sessionId,
        string checkpointId,
        string requestId,
        ReturnDecision decision,
        CancellationToken ct = default,
        IProgress<OrchestrationEvent>? events = null
    )
    {
        ArgumentNullException.ThrowIfNull(checkpoints);
        ArgumentNullException.ThrowIfNull(decision);

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

        await run.SendResponseAsync(pending.CreateResponse(decision));

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

    public Task<WorkflowState> ResumeAsync(WorkflowState state, bool approved, CancellationToken ct = default) =>
        ResumeAsync(state, ReturnDecision.FromApproved(approved), ct);

    public async Task<WorkflowState> ResumeAsync(
        WorkflowState state,
        ReturnDecision decision,
        CancellationToken ct = default
    )
    {
        ArgumentNullException.ThrowIfNull(state);
        ArgumentNullException.ThrowIfNull(decision);
        if (!_pausedRuns.Remove(state.OrderId, out var paused))
        {
            throw new InvalidOperationException(
                "Workflow is not waiting on a HITL response; call ExecuteAsync first."
            );
        }

        await paused.Run.SendResponseAsync(paused.Request.CreateResponse(decision));

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
                result = await tools.InitiateReturnAsync(state.OrderId, reason, RefundMethodFor(state.Resolution), ct);
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

            if (state.AmountOverride is { } edited && edited != state.RefundAmount)
            {
                // Honour the reviewer's edited amount on the return that was just created. A failure is an
                // error, not a silent full refund.
                AdjustReturnResult adjusted;
                try
                {
                    adjusted = await tools.AdjustReturnAmountAsync(state.ReturnId ?? "", edited, ct);
                }
                catch (Exception ex) when (ex is not OperationCanceledException)
                {
                    state.Errors.Add($"adjust_return_amount: {ex.Message}");
                    await context.YieldOutputAsync(state, ct);
                    return;
                }
                if (!adjusted.Success)
                {
                    state.Errors.Add($"adjust_return_amount: {adjusted.Error ?? "not applied"}");
                    await context.YieldOutputAsync(state, ct);
                    return;
                }
                state.RefundAmount = edited;
            }
            await context.SendMessageAsync(state, ct);
        }

        /// <summary>replacement is paid out as store credit toward the replacement.</summary>
        private static string RefundMethodFor(string resolution) =>
            resolution == "refund" ? "original_payment" : "store_credit";
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
    /// Routes the request through the approval policy — the .NET analog of Python's
    /// <c>_PolicyGateExecutor.run</c>. Sends to <paramref name="approvalPortId"/> (pausing the workflow)
    /// unless the policy routes it <c>auto</c>, in which case it goes straight on to initiate-return.
    /// Explicit <c>targetId</c> routing since the two outbound message types differ
    /// (<see cref="ReturnApprovalRequest"/> vs. <see cref="WorkflowState"/>).
    /// If the policy cannot decide (<c>null</c>) the <paramref name="threshold"/> decides: an order
    /// total strictly above it is reviewed by support.
    /// </summary>
    [SendsMessage(typeof(ReturnApprovalRequest))]
    [SendsMessage(typeof(WorkflowState))]
    [YieldsOutput(typeof(WorkflowState))]
    private sealed class GateDecisionExecutor(decimal threshold, IReturnPolicy? policy, string approvalPortId)
        : Executor<WorkflowState>("policy-gate")
    {
        public override async ValueTask HandleAsync(WorkflowState state, IWorkflowContext context, CancellationToken ct = default)
        {
            state.CompletedSteps.Add("policy_gate");

            PolicyDecision? decision = null;
            if (policy is not null)
            {
                decision = await policy.DecideAsync(state, ct);
            }

            string route;
            IReadOnlyList<string> reasons;
            string version;
            if (decision is not null)
            {
                (route, reasons, version) = (decision.Route, decision.Reasons, decision.Version);
            }
            else
            {
                var over = state.OrderTotal > threshold;
                route = over ? "support" : "auto";
                reasons = [over
                    ? $"Policy engine unavailable; order total is over the {threshold:F2} manual-review threshold"
                    : "Policy engine unavailable; order total is within the auto-approval threshold"];
                version = "";
            }

            if (route == "auto")
            {
                state.HitlApproved = true;
                await context.SendMessageAsync(state, ct);
                return;
            }

            state.HitlRequested = true;
            state.RefundAmount = state.OrderTotal; // the amount under review; no return exists yet
            // Snapshot so a caller observing the stream sees the
            // pause state before the RequestInfoEvent actually pauses the run.
            await context.YieldOutputAsync(state, ct);

            // Park the state in the workflow's own scope, because the port
            // carries a ReturnApprovalRequest in and a ReturnDecision out — the state
            // itself does not cross it. Checkpointing captures this scope, so
            // it is what lets a rebuilt graph pick up where this one stopped;
            // a field on an executor would not survive the restore.
            await context.QueueStateUpdateAsync(StateKey, state, ScopeName, ct);

            var request = new ReturnApprovalRequest(
                state.OrderId, state.OrderTotal, state.OrderTotal, 0, route, reasons, version);
            await context.SendMessageAsync(request, approvalPortId, ct);
        }
    }

    /// <summary>
    /// Fed by the <see cref="RequestPort"/> once a decision arrives.
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
    private sealed class PolicyResumeExecutor() : Executor<ReturnDecision>("policy-resume")
    {
        public override async ValueTask HandleAsync(ReturnDecision decision, IWorkflowContext context, CancellationToken ct = default)
        {
            var state = await context.ReadStateAsync<WorkflowState>(StateKey, ScopeName, ct)
                ?? throw new InvalidOperationException(
                    $"No '{StateKey}' in scope '{ScopeName}' — the gate must park the state before the port pauses.");

            state.HitlRequested = true;
            state.ApprovalId = string.IsNullOrEmpty(decision.ApprovalId) ? null : decision.ApprovalId;
            state.DecidedBy = string.IsNullOrEmpty(decision.DecidedBy) ? null : decision.DecidedBy;
            if (!decision.IsApproval)
            {
                state.HitlApproved = false;
                state.Errors.Add("policy_gate: return rejected by reviewer");
                await context.YieldOutputAsync(state, ct);
                return;
            }

            state.HitlApproved = true;
            if (ReturnDecision.Resolutions.Contains(decision.Resolution))
            {
                state.Resolution = decision.Resolution;
            }
            if (decision.Amount is { } amount)
            {
                state.AmountOverride = amount;
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
