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

    // Policy gate / reviewer decision
    public bool HitlRequested { get; set; }
    public bool? HitlApproved { get; set; }
    /// <summary>refund | store_credit | replacement — what the return is resolved as. A reviewer may change it.</summary>
    public string Resolution { get; set; } = "store_credit";
    /// <summary>A reviewer's edited refund amount; null keeps what <c>initiate_return</c> created.</summary>
    public decimal? AmountOverride { get; set; }
    public string? ApprovalId { get; set; }
    public string? DecidedBy { get; set; }

    // Tracking
    public List<string> CompletedSteps { get; set; } = new();
    public List<string> Errors { get; set; } = new();

    public sealed record LoyaltyDiscount(string? Tier, decimal DiscountPct);
}

/// <summary>
/// What the policy gate emits when a person has to decide. No return exists yet, so
/// <see cref="RefundAmount"/> is the amount under review: the order total. The policy fields are
/// what an inbox shows the reviewer. (The full <see cref="WorkflowState"/> is parked in the
/// workflow's own checkpointed scope rather than carried here — .NET's port carries a request in
/// and a decision out, and the scope is what survives a restore.)
/// </summary>
public sealed record ReturnApprovalRequest(
    string OrderId,
    decimal OrderTotal,
    decimal RefundAmount,
    int ReplacementCount,
    string Route = "support",
    IReadOnlyList<string>? Reasons = null,
    string PolicyVersion = ""
);

/// <summary>
/// A reviewer's answer to a <see cref="ReturnApprovalRequest"/>. <see cref="Decision"/> is
/// <c>approve</c> or <c>reject</c>; anything else counts as a rejection (an expiry resumes as one).
/// </summary>
public sealed record ReturnDecision(
    string Decision = "reject",
    string Resolution = "store_credit",
    decimal? Amount = null,
    string Note = "",
    string DecidedBy = "",
    string ApprovalId = ""
)
{
    public static readonly string[] Resolutions = ["refund", "store_credit", "replacement"];

    public bool IsApproval => string.Equals(Decision, "approve", StringComparison.OrdinalIgnoreCase);

    public static ReturnDecision FromApproved(bool approved) => new(approved ? "approve" : "reject");
}
