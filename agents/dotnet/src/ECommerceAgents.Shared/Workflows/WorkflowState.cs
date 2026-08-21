using System.Text.Json;

namespace ECommerceAgents.Shared.Workflows;

/// <summary>
/// Mutable state for the return-and-replace workflow. Mirrors
/// <c>WorkflowState</c> in
/// <c>agents/python/workflows/return_replace.py</c>.
/// </summary>
/// <remarks>
/// The three collections below have public setters for a load-bearing reason, not
/// tidiness. Checkpoint-based resume (#33) round-trips this object through storage, and
/// System.Text.Json cannot fill a get-only collection on a type with a parameterized
/// constructor — it builds the state and leaves them <b>empty, with no error</b>. A
/// resumed workflow would look entirely plausible while having lost the steps it had
/// already run and the return it had already opened.
/// (<c>JsonObjectCreationHandling.Populate</c> is the tidier fix and throws
/// <c>NotSupportedException</c> here, for exactly that constructor reason.)
/// Covered by <c>WorkflowStateRoundTripTests</c>.
/// </remarks>
public sealed class WorkflowState
{
    public WorkflowState(string userEmail, string orderId)
    {
        UserEmail = userEmail;
        OrderId = orderId;
    }

    public string UserEmail { get; init; }
    public string OrderId { get; init; }
    public decimal OrderTotal { get; set; }
    public string Reason { get; set; } = "";

    // Populated along the chain
    public bool ReturnEligible { get; set; }
    public string? ReturnId { get; set; }
    public decimal RefundAmount { get; set; }
    public List<JsonElement> ReplacementProducts { get; set; } = new();
    public LoyaltyDiscount? AppliedDiscount { get; set; }

    // HITL
    public bool HitlRequested { get; set; }
    public bool? HitlApproved { get; set; }

    // Tracking
    public List<string> CompletedSteps { get; set; } = new();
    public List<string> Errors { get; set; } = new();

    public sealed record LoyaltyDiscount(string? Tier, decimal DiscountPct);
}

/// <summary>
/// Snapshot emitted at the HITL gate for high-value returns. Callers
/// render this in an approval UI and call
/// <see cref="ReturnAndReplaceWorkflow.ResumeAsync"/> with the decision.
/// </summary>
public sealed record ReturnApprovalRequest(
    string OrderId,
    decimal OrderTotal,
    decimal RefundAmount,
    int ReplacementCount
);
