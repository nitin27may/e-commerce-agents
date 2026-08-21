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
    string? ConversationId = null
);

/// <summary>A mode's answer for one turn.</summary>
public sealed record ModeRunResult(
    string Text,
    IReadOnlyList<string> AgentsInvolved,
    int StepCount
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
