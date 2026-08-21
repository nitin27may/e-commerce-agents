using Dapper;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Orchestration;
using ECommerceAgents.Shared.Workflows;
using System.Text.RegularExpressions;

namespace ECommerceAgents.Orchestrator.Modes;

/// <summary>
/// Sequential return-and-replace: check eligibility, initiate the return,
/// find replacements, gate high-value refunds on a human, then apply a
/// loyalty discount and finalise. Wraps <see cref="ReturnAndReplaceWorkflow"/>
/// and its <c>RequestPort</c> HITL gate. Mirrors Python's
/// <c>ReturnReplaceMode</c>.
/// </summary>
/// <remarks>
/// The workflow takes an order id; a chat message is free text. So this
/// resolves one the way Python's mode does — a UUID literal if present,
/// otherwise the caller's most recent order. Falling back to "most recent"
/// rather than guessing is deliberate: a return is destructive, and picking
/// an arbitrary matching order would be the worst possible kind of helpful.
/// </remarks>
public sealed partial class ReturnReplaceMode(DatabasePool pool, AgentSettings settings) : IOrchestrationMode
{
    private readonly DatabasePool _pool = pool;
    private readonly AgentSettings _settings = settings;

    public string Name => "workflow:return-replace";
    public string Label => "Return & Replace (sequential + in-workflow HITL)";
    public string Description =>
        "Checks return eligibility, initiates the return, suggests replacements, and pauses for approval on high-value refunds.";

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
            check_eligibility[Check eligibility] --> initiate_return[Initiate return]
            initiate_return --> search_replacements[Search replacements]
            search_replacements --> hitl_gate{High value?}
            hitl_gate -->|approval needed| hitl_resume[Await approval]
            hitl_gate -->|under threshold| apply_discount[Apply loyalty discount]
            hitl_resume --> apply_discount
            apply_discount --> finalize[Finalize]
        """;

    [GeneratedRegex(@"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")]
    private static partial Regex UuidPattern();

    public async Task<ModeRunResult> RunAsync(string message, RunContext ctx, CancellationToken ct = default)
    {
        var email = RequestContext.CurrentUserEmail;
        if (string.IsNullOrEmpty(email))
        {
            return new ModeRunResult("You'll need to sign in before I can start a return.", ["orchestrator"], 0);
        }

        var order = await ResolveOrderAsync(message, email, ct);
        if (order is null)
        {
            return new ModeRunResult(
                "I couldn't find an order to return. Tell me the order id and I'll take it from there.",
                ["orchestrator"],
                0
            );
        }

        // Threshold comes from settings so .NET and Python gate at the same
        // value rather than each hardcoding one.
        var workflow = new ReturnAndReplaceWorkflow(
            new ReturnReplaceTools(_pool, _settings),
            (decimal)_settings.ReturnHitlThreshold
        );
        // OrderTotal is what GateDecisionExecutor compares against the
        // approval threshold. Leaving it at its default of 0 meant a $603
        // refund sailed past a $500 gate — caught only by running it, since
        // every workflow test supplies the total itself.
        var state = await workflow.ExecuteAsync(
            new WorkflowState(email, order.Value.Id) { Reason = message, OrderTotal = order.Value.Total },
            ct,
            ctx.Events
        );

        return new ModeRunResult(
            Summarise(state),
            ["order-management", "product-discovery", "pricing-promotions"],
            state.CompletedSteps.Count
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
            parts.Add("This refund is above the approval threshold, so it's waiting on a human before the refund is released.");
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
