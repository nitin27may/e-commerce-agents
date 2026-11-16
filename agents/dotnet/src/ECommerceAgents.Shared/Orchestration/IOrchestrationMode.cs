using ECommerceAgents.Shared.Context;

namespace ECommerceAgents.Shared.Orchestration;

/// <summary>
/// What a mode supports, surfaced by <c>GET /api/orchestration/modes</c> so
/// the UI can render capability chips without hardcoding per-mode knowledge.
/// Mirrors Python's <c>ModeCapabilities</c>.
/// </summary>
public sealed record ModeCapabilities(
    bool Streams = true,
    bool SupportsHitl = false,
    bool SupportsCheckpoints = false,
    bool IsGraph = false
);

/// <summary>
/// Request-scoped, non-identity state handed to every mode.
/// </summary>
/// <remarks>
/// Identity is deliberately absent: this codebase carries user email, role and
/// session in <c>RequestContext</c> and reads them where they're needed, never
/// threading them as parameters. A mode that took an email argument would be
/// one the model could lie to.
/// </remarks>
public sealed record RunContext(
    IReadOnlyList<HistoryEntry> History,
    string? ConversationId = null,
    /// <summary>
    /// Where a mode reports progress as it happens. Null when nobody is
    /// listening — the non-streaming chat endpoint and mode comparison both
    /// want only the final result, and a mode must not have to care which.
    /// </summary>
    IProgress<OrchestrationEvent>? Events = null
);

/// <summary>A mode's answer for one turn.</summary>
/// <param name="PendingApproval">True when the run stopped on a human.</param>
/// <param name="RequestId">MAF's resume token for that pause.</param>
/// <param name="LatestCheckpointId">The checkpoint a later request resumes from.</param>
/// <param name="SessionId">Keys the checkpoints this run wrote.</param>
/// <param name="NoLlmCalls">
/// True when the mode makes no chat-model call (the two workflow modes), so a turn's
/// zero tokens is a measured zero rather than "usage was not captured".
/// </param>
/// <param name="Approval">The gate's routing decision and the order under review, when the run paused.</param>
/// <param name="Completed">
/// For a resumed run: whether the workflow ran to its last step (a return that was created and finalised). Null when
/// the mode does not say, which the approvals service reads as completed.
/// </param>
/// <remarks>
/// The four pause fields are defaulted so modes that never pause are unaffected. They
/// exist because a pause previously could not leave the mode at all: the RequestId
/// surfaced only as a progress report, so nothing could persist it — which is why .NET
/// had a pending-approval badge nothing could resume.
/// </remarks>
public sealed record ModeRunResult(
    string Text,
    IReadOnlyList<string> AgentsInvolved,
    int StepCount,
    bool PendingApproval = false,
    string? RequestId = null,
    string? LatestCheckpointId = null,
    string? SessionId = null,
    bool NoLlmCalls = false,
    PendingApprovalInfo? Approval = null,
    bool? Completed = null
);

/// <summary>
/// What a paused return carries to the inbox: the policy gate's own routing decision (so the approval is routed by what actually
/// paused the run, not a second evaluation) and the order and amount under review.
/// </summary>
/// <param name="Route"><c>auto</c>, <c>support</c> or <c>manager</c> — the gate's decision.</param>
/// <param name="Reasons">Why, from the matched rule.</param>
/// <param name="PolicyVersion">The policy file's version at that moment.</param>
/// <param name="OrderId">The order the return is for.</param>
/// <param name="OrderTotal">The amount under review.</param>
public sealed record PendingApprovalInfo(
    string Route,
    IReadOnlyList<string> Reasons,
    string PolicyVersion,
    string OrderId,
    decimal OrderTotal
);

/// <summary>
/// One way of turning a user message into a response — the .NET twin of
/// Python's <c>OrchestrationMode</c> protocol.
/// </summary>
/// <remarks>
/// An interface rather than Python's structural Protocol, because C# has no
/// structural typing and DI needs something to resolve against.
///
/// <see cref="GraphMermaid"/> returns null for modes that decide their route
/// per turn — the tool router is an LLM picking a specialist, which has no
/// fixed topology to draw. Workflow-backed modes return real Mermaid.
/// </remarks>
public interface IOrchestrationMode
{
    string Name { get; }
    string Label { get; }
    string Description { get; }
    ModeCapabilities Capabilities { get; }

    /// <summary>Static Mermaid source, or null when the mode has no fixed graph.</summary>
    string? GraphMermaid();

    Task<ModeRunResult> RunAsync(string message, RunContext ctx, CancellationToken ct = default);
}
