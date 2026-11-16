using Dapper;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Orchestration;
using ECommerceAgents.Shared.Policy;
using ECommerceAgents.Shared.Workflows;
using Microsoft.Agents.AI.Workflows;
using System.Text.RegularExpressions;

namespace ECommerceAgents.Orchestrator.Modes;

/// <summary>
/// Sequential return-and-replace: check eligibility, ask the approval policy
/// whether a person must decide (pausing on a <c>RequestPort</c> if so), and only
/// then initiate the return, find replacements, apply a loyalty discount and
/// finalise. Wraps <see cref="ReturnAndReplaceWorkflow"/>. Mirrors Python's
/// <c>ReturnReplaceMode</c>.
/// </summary>
/// <remarks>
/// The workflow takes an order id; a chat message is free text. So this
/// resolves one the way Python's mode does — a UUID literal if present,
/// otherwise the caller's most recent order. Falling back to "most recent"
/// rather than guessing is deliberate: a return is destructive, and picking
/// an arbitrary matching order would be the worst possible kind of helpful.
/// </remarks>
public sealed partial class ReturnReplaceMode(DatabasePool pool, AgentSettings settings, CheckpointManager checkpoints) : IOrchestrationMode, IResumableMode
{
    private readonly DatabasePool _pool = pool;
    private readonly CheckpointManager _checkpoints = checkpoints;
    private readonly AgentSettings _settings = settings;

    public string Name => "workflow:return-replace";
    public string Label => "Return & Replace (sequential + in-workflow HITL)";
    public string Description =>
        "Checks return eligibility, asks the approval policy whether a person must decide (pausing before anything is created), then initiates the return, suggests replacements and applies a loyalty discount.";

    public ModeCapabilities Capabilities => new(
        Streams: false,
        SupportsHitl: true,
        SupportsCheckpoints: true,
        IsGraph: true
    );

    /// <summary>
    /// Node ids mirror the workflow's executor ids with dashes as underscores,
    /// the convention that lets the UI correlate a live node event to this
    /// diagram.
    /// </summary>
    public string? GraphMermaid() => """
        graph TD
            check_eligibility[Check eligibility] --> policy_gate{Policy route?}
            policy_gate -->|support or manager| policy_resume[Await approval]
            policy_gate -->|auto| initiate_return[Initiate return]
            policy_resume --> initiate_return
            initiate_return --> search_replacements[Search replacements]
            search_replacements --> apply_discount[Apply loyalty discount]
            apply_discount --> finalize[Finalize]
        """;

    [GeneratedRegex(@"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")]
    private static partial Regex UuidPattern();

    /// <summary>
    /// The approval policy, read from the SAME <c>config/policies/approvals.yaml</c> as Python. When the file
    /// cannot be found or is invalid this FAILS CLOSED: every return goes to a manager (never auto-approved by
    /// the <c>ReturnHitlThreshold</c>) and an error is logged.
    /// </summary>
    private IReturnPolicy Policy()
    {
        var path = PolicyFile();
        if (path is null)
        {
            Serilog.Log.Error("approvals.yaml not found; every return is routed to a manager until it is");
            return new FailClosedReturnPolicy();
        }
        try
        {
            return new ReturnPolicyGate(ApprovalPolicy.LoadFile(path), _pool);
        }
        catch (PolicyException ex)
        {
            Serilog.Log.Error(ex, "approvals.yaml is invalid; every return is routed to a manager until it is fixed");
            return new FailClosedReturnPolicy();
        }
    }

    /// <summary>
    /// Validate the policy file once at startup so a broken one is an error in the log before the first return.
    /// Does not throw: the gate fails closed on its own.
    /// </summary>
    public static bool CheckPolicyFile()
    {
        var path = PolicyFile();
        if (path is null)
        {
            Serilog.Log.Error("approval_policy_invalid: approvals.yaml not found; every return will be routed to a manager");
            return false;
        }
        try
        {
            ApprovalPolicy.LoadFile(path).Kind(ReturnPolicyGate.Kind);
            return true;
        }
        catch (PolicyException ex)
        {
            Serilog.Log.Error(ex, "approval_policy_invalid: approvals.yaml could not be loaded; every return will be routed to a manager");
            return false;
        }
    }

    private static string? PolicyFile()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null)
        {
            var repoCopy = Path.Combine(dir.FullName, "agents", "python", "config", "policies", "approvals.yaml");
            if (File.Exists(repoCopy)) return repoCopy;
            dir = dir.Parent;
        }
        var beside = Path.Combine(AppContext.BaseDirectory, "config", "policies", "approvals.yaml");
        return File.Exists(beside) ? beside : null;
    }

    private ReturnAndReplaceWorkflow NewWorkflow() => new(
        new ReturnReplaceTools(_pool, _settings),
        (decimal)_settings.ReturnHitlThreshold,
        Policy()
    );

    /// <inheritdoc />
    public Task<ModeRunResult> ResumeAsync(
        string sessionId,
        string checkpointId,
        string requestId,
        bool approved,
        CancellationToken ct = default
    ) => ResumeAsync(sessionId, checkpointId, requestId, ReturnDecision.FromApproved(approved), ct);

    /// <summary>
    /// Resumes with the reviewer's full answer — the resolution (refund / store_credit / replacement), an edited
    /// refund amount, a note — the same <see cref="ReturnDecision"/> shape Python answers. Not on
    /// <see cref="IResumableMode"/>: the resume route only carries approve/reject until the approvals API is
    /// ported (D-5n).
    /// </summary>
    public async Task<ModeRunResult> ResumeAsync(
        string sessionId,
        string checkpointId,
        string requestId,
        ReturnDecision decision,
        CancellationToken ct = default
    )
    {
        // A fresh workflow object, exactly as a later process would get: the run that
        // paused lived in a previous request and is long gone. Everything it knew comes
        // back from the checkpoint.
        var workflow = NewWorkflow();

        var state = await workflow.ResumeFromCheckpointAsync(
            _checkpoints, sessionId, checkpointId, requestId, decision, ct);

        return new ModeRunResult(
            Summarise(state),
            ["order-management", "product-discovery", "pricing-promotions"],
            state.CompletedSteps.Count,
            NoLlmCalls: true
        );
    }

    public async Task<ModeRunResult> RunAsync(string message, RunContext ctx, CancellationToken ct = default)
    {
        var email = RequestContext.CurrentUserEmail;
        if (string.IsNullOrEmpty(email))
        {
            return new ModeRunResult("You'll need to sign in before I can start a return.", ["orchestrator"], 0, NoLlmCalls: true);
        }

        var order = await ResolveOrderAsync(message, email, ct);
        if (order is null)
        {
            return new ModeRunResult(
                "I couldn't find an order to return. Tell me the order id and I'll take it from there.",
                ["orchestrator"],
                0,
                NoLlmCalls: true
            );
        }

        // Threshold comes from settings so .NET and Python gate at the same
        // value rather than each hardcoding one.
        var workflow = NewWorkflow();
        // OrderTotal is the amount the policy gate evaluates (and the fallback threshold compares). Leaving it at its default of 0 meant a $603
        // refund sailed past a $500 gate — caught only by running it, since
        // every workflow test supplies the total itself.
        var outcome = await workflow.RunAsync(
            new WorkflowState(email, order.Value.Id) { Reason = message, OrderTotal = order.Value.Total },
            ct,
            ctx.Events,
            _checkpoints
        );

        return new ModeRunResult(
            Summarise(outcome.State),
            ["order-management", "product-discovery", "pricing-promotions"],
            outcome.State.CompletedSteps.Count,
            PendingApproval: outcome.PendingRequestId is not null,
            RequestId: outcome.PendingRequestId,
            LatestCheckpointId: outcome.LastCheckpointId,
            SessionId: outcome.SessionId,
            NoLlmCalls: true
        );
    }

    private async Task<(string Id, decimal Total)?> ResolveOrderAsync(string message, string email, CancellationToken ct)
    {
        await using var conn = await _pool.OpenAsync();

        var literal = UuidPattern().Match(message);
        if (literal.Success && Guid.TryParse(literal.Value, out var explicitId))
        {
            // Scoped by email even for an explicit id, so a guessed order id
            // can't pull another user's total into the gate decision.
            var found = await conn.QueryFirstOrDefaultAsync(
                @"SELECT o.id, o.total FROM orders o JOIN users u ON o.user_id = u.id
                  WHERE o.id = @id AND u.email = @email",
                new { id = explicitId, email }
            );
            return found is null ? null : (((Guid)found.id).ToString(), (decimal)found.total);
        }

        var recent = await conn.QueryFirstOrDefaultAsync(
            @"SELECT o.id, o.total FROM orders o JOIN users u ON o.user_id = u.id
              WHERE u.email = @email
              ORDER BY o.created_at DESC LIMIT 1",
            new { email }
        );
        return recent is null ? null : (((Guid)recent.id).ToString(), (decimal)recent.total);
    }

    private static string Summarise(WorkflowState state)
    {
        if (!state.ReturnEligible)
        {
            var reason = state.Errors.Count > 0 ? state.Errors[0] : "this order isn't eligible for a return";
            return $"I couldn't start a return: {reason}";
        }

        var parts = new List<string>();

        if (state.ReturnId is not null)
        {
            parts.Add($"Return {state.ReturnId[..Math.Min(8, state.ReturnId.Length)]} created for ${state.RefundAmount:F2}.");
        }

        if (state.HitlRequested && state.HitlApproved is null)
        {
            // The pause is the answer here, not a failure — say so plainly
            // rather than implying the return is complete.
            // The pause comes BEFORE anything is created: no return exists yet.
            parts.Add($"This return needs approval before anything is changed (${state.OrderTotal:F2}); a person will review it.");
        }
        else if (state.HitlApproved == false)
        {
            parts.Add("An approver declined this refund.");
        }

        if (state.AppliedDiscount is { } discount && discount.DiscountPct > 0)
        {
            parts.Add($"Your {discount.Tier} tier adds {discount.DiscountPct:F0}% off a replacement.");
        }

        if (state.ReplacementProducts.Count > 0)
        {
            parts.Add($"I found {state.ReplacementProducts.Count} replacement option(s).");
        }

        return parts.Count > 0
            ? string.Join(" ", parts)
            : "I started the return but couldn't retrieve the details.";
    }
}
