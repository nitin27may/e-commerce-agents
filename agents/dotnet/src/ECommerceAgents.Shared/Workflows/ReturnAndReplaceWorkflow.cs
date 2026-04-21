namespace ECommerceAgents.Shared.Workflows;

/// <summary>
/// Sequential return-and-replace workflow with a human-in-the-loop
/// approval gate for high-value orders. .NET parity port of
/// <c>agents/python/workflows/return_replace.py</c>.
/// <para>
/// Step chain: check-eligibility → initiate-return → search-replacements
/// → hitl-gate → apply-discount → finalize.
/// </para>
/// <para>
/// HITL contract:
/// </para>
/// <list type="bullet">
///   <item><description>
///     When <c>OrderTotal</c> is at or below <see cref="_threshold"/>,
///     the workflow continues through to finalize and
///     <see cref="WorkflowState.HitlApproved"/> is set to <c>true</c>.
///   </description></item>
///   <item><description>
///     When <c>OrderTotal</c> is above the threshold,
///     <see cref="ExecuteAsync"/> pauses and returns with
///     <c>HitlRequested=true</c> and <c>HitlApproved=null</c>. The
///     caller renders an approval UI and calls
///     <see cref="ResumeAsync"/> with the decision.
///   </description></item>
/// </list>
/// <para>
/// The original Python implementation uses MAF's
/// <c>ctx.request_info</c> to suspend the MAF Workflow. This .NET
/// port preserves the *observable* contract (same state progression,
/// same step IDs, same error messages) while using an idiomatic
/// two-call pause/resume pattern on top of sequential async code.
/// </para>
/// </summary>
public sealed class ReturnAndReplaceWorkflow
{
    private readonly IReturnReplaceTools _tools;
    private readonly decimal _threshold;

    public ReturnAndReplaceWorkflow(IReturnReplaceTools tools, decimal hitlThreshold = 500m)
    {
        _tools = tools ?? throw new ArgumentNullException(nameof(tools));
        _threshold = hitlThreshold;
    }

    // ─────────────────────── execute ─────────────────────────

    public async Task<WorkflowState> ExecuteAsync(WorkflowState state, CancellationToken ct = default)
    {
        ArgumentNullException.ThrowIfNull(state);

        if (!await CheckEligibilityAsync(state, ct)) return state;
        if (!await InitiateReturnAsync(state, ct)) return state;
        await SearchReplacementsAsync(state, ct);

        state.CompletedSteps.Add("hitl_gate");
        if (state.OrderTotal > _threshold)
        {
            state.HitlRequested = true;
            return state; // pause; caller must ResumeAsync
        }

        state.HitlApproved = true;
        await ApplyDiscountAsync(state, ct);
        state.CompletedSteps.Add("finalize");
        return state;
    }

    // ─────────────────────── resume ──────────────────────────

    public async Task<WorkflowState> ResumeAsync(
        WorkflowState state,
        bool approved,
        CancellationToken ct = default
    )
    {
        ArgumentNullException.ThrowIfNull(state);
        if (!state.HitlRequested)
        {
            throw new InvalidOperationException(
                "Workflow is not waiting on a HITL response; call ExecuteAsync first."
            );
        }

        state.HitlApproved = approved;
        if (!approved)
        {
            state.Errors.Add("hitl_gate: return rejected by reviewer");
            return state;
        }

        await ApplyDiscountAsync(state, ct);
        state.CompletedSteps.Add("finalize");
        return state;
    }

    public ReturnApprovalRequest BuildApprovalRequest(WorkflowState state) =>
        new(state.OrderId, state.OrderTotal, state.RefundAmount, state.ReplacementProducts.Count);

    // ─────────────────────── steps ───────────────────────────

    private async Task<bool> CheckEligibilityAsync(WorkflowState state, CancellationToken ct)
    {
        try
        {
            var result = await _tools.CheckReturnEligibilityAsync(state.OrderId, ct);
            state.ReturnEligible = result.Eligible;
            state.CompletedSteps.Add("check_eligibility");
            if (!result.Eligible)
            {
                state.Errors.Add(result.Reason ?? "Not eligible for return");
                return false;
            }
            return true;
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            state.Errors.Add($"check_eligibility: {ex.Message}");
            return false;
        }
    }

    private async Task<bool> InitiateReturnAsync(WorkflowState state, CancellationToken ct)
    {
        try
        {
            var reason = string.IsNullOrWhiteSpace(state.Reason)
                ? "Customer requested replacement"
                : state.Reason;
            var result = await _tools.InitiateReturnAsync(state.OrderId, reason, "store_credit", ct);
            if (!string.IsNullOrEmpty(result.Error))
            {
                state.Errors.Add($"initiate_return: {result.Error}");
                return false;
            }
            state.ReturnId = result.ReturnId;
            state.RefundAmount = result.RefundAmount;
            state.CompletedSteps.Add("initiate_return");
            return true;
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            state.Errors.Add($"initiate_return: {ex.Message}");
            return false;
        }
    }

    private async Task SearchReplacementsAsync(WorkflowState state, CancellationToken ct)
    {
        try
        {
            var results = await _tools.SearchReplacementsAsync(
                maxPrice: state.RefundAmount * 1.2m,
                minRating: 4.0m,
                limit: 5,
                ct
            );
            state.ReplacementProducts.AddRange(results);
            state.CompletedSteps.Add("search_replacements");
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            state.Errors.Add($"search_replacements: {ex.Message}");
        }
    }

    private async Task ApplyDiscountAsync(WorkflowState state, CancellationToken ct)
    {
        try
        {
            var info = await _tools.GetLoyaltyTierAsync(ct);
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
    }
}
