namespace ECommerceAgents.Shared.Orchestration;

/// <summary>
/// One thing that happened during a mode run — the .NET twin of Python's
/// <c>orchestrator/events.py::OrchestrationEvent</c>.
/// </summary>
/// <remarks>
/// Exists so the five mechanisms a mode can be built on (a tool-calling agent,
/// a workflow graph, a handoff mesh…) emit one shape, and the UI never has to
/// know which produced a given frame.
///
/// The wire contract is already fixed by <c>web/src/lib/api.ts</c>'s
/// <c>onOrchestrationEvent</c> hook, which Python has been feeding since
/// Phase 1 — so this matches that rather than inventing a second shape. The
/// client routes any SSE event name outside <c>step</c>/<c>metadata</c>/
/// <c>delta</c>/plain-text here, and .NET emitted none of them until now,
/// which is why the live graph never animated on the .NET backend.
/// </remarks>
public sealed record OrchestrationEvent(
    string Kind,
    string? NodeId = null,
    string? Agent = null,
    IReadOnlyDictionary<string, object?>? Payload = null
)
{
    /// <summary>Node ids reach the UI with dashes as underscores.</summary>
    /// <remarks>
    /// Not cosmetic: the client correlates a live node event to a node in the
    /// mode's Mermaid diagram by id, and Mermaid node ids can't contain
    /// dashes. Both stacks' <c>GraphMermaid()</c> emit the underscored form,
    /// so the translation has to happen here too or nothing lights up.
    /// </remarks>
    public static string ToNodeId(string executorId) => executorId.Replace('-', '_');

    public static OrchestrationEvent NodeEnter(string executorId) =>
        new("node_enter", ToNodeId(executorId));

    public static OrchestrationEvent NodeExit(string executorId) =>
        new("node_exit", ToNodeId(executorId));

    public static OrchestrationEvent NodeError(string executorId, string message) =>
        new("error", ToNodeId(executorId), Payload: new Dictionary<string, object?> { ["message"] = message });

    public static OrchestrationEvent RequestInfo(string executorId, string? requestId) =>
        new("request_info", ToNodeId(executorId), Payload: new Dictionary<string, object?>
        {
            ["request_id"] = requestId,
        });
}
