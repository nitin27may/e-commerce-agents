using System.Threading.Channels;

namespace ECommerceAgents.Shared.Context;

/// <summary>
/// Request-scoped identity + conversation context.
/// </summary>
/// <remarks>
/// Mirrors Python's <c>shared/context.py</c> ContextVars. In .NET the
/// equivalent is <see cref="AsyncLocal{T}"/>, which propagates across
/// <c>async</c> boundaries within the same logical flow. Middleware
/// populates the context from JWT claims or the inter-agent auth
/// headers before the route handler runs; tools read it directly
/// instead of threading parameters through call stacks.
/// </remarks>
public static class RequestContext
{
    private static readonly AsyncLocal<string?> _userEmail = new();
    private static readonly AsyncLocal<string?> _userRole = new();
    private static readonly AsyncLocal<string?> _sessionId = new();
    private static readonly AsyncLocal<IReadOnlyList<HistoryEntry>?> _history = new();
    private static readonly AsyncLocal<List<string>?> _invokedAgents = new();
    private static readonly AsyncLocal<ChannelWriter<string>?> _streamWriter = new();
    private static readonly AsyncLocal<Dictionary<string, bool>?> _guardrailFlags = new();
    private static readonly AsyncLocal<List<ExecutionStep>?> _steps = new();

    public static string CurrentUserEmail
    {
        get => _userEmail.Value ?? string.Empty;
        set => _userEmail.Value = value;
    }

    public static string CurrentUserRole
    {
        get => _userRole.Value ?? string.Empty;
        set => _userRole.Value = value;
    }

    public static string CurrentSessionId
    {
        get => _sessionId.Value ?? string.Empty;
        set => _sessionId.Value = value;
    }

    public static IReadOnlyList<HistoryEntry> CurrentHistory
    {
        get => _history.Value ?? Array.Empty<HistoryEntry>();
        set => _history.Value = value;
    }

    /// <summary>
    /// Specialist agents invoked so far during the current chat turn — appended to by
    /// <c>OrchestratorTools.CallSpecialistAgent</c> on every A2A call. Backs the
    /// streaming chat endpoint's dynamic <c>agents_involved</c>, mirroring Python's
    /// <c>current_steps</c> ContextVar (<c>orchestrator/routes.py:655</c>). The
    /// blocking endpoint doesn't need this — Python's own blocking handler never
    /// grows <c>agents_involved</c> past <c>["orchestrator"]</c> either.
    /// </summary>
    public static IReadOnlyList<string> CurrentInvokedAgents =>
        (IReadOnlyList<string>?)_invokedAgents.Value ?? Array.Empty<string>();

    /// <summary>Records that <paramref name="agentName"/> was called via A2A during this request.</summary>
    public static void RecordInvokedAgent(string agentName) => _invokedAgents.Value?.Add(agentName);

    /// <summary>
    /// Set only around a streaming chat turn (<c>ChatRoutes.StreamAsync</c>) — the
    /// .NET analog of Python's <c>current_stream_queue</c> ContextVar
    /// (<c>orchestrator/agent.py</c>). <c>OrchestratorTools.CallSpecialistAgent</c>
    /// writes each specialist delta here as it streams from
    /// <c>A2AClient.StreamAsync</c>, so the outer HTTP response can forward a live
    /// preview of the specialist's answer (<c>event: delta</c>) while the
    /// orchestrator's own agent run is still blocked awaiting that tool call.
    /// <c>null</c> outside a streaming turn (blocking <c>/api/chat</c>, or any
    /// caller that never opened a scope) — every write site must treat that as a
    /// safe no-op, matching <see cref="RecordInvokedAgent"/>'s own null-safety.
    /// </summary>
    public static ChannelWriter<string>? CurrentStreamWriter => _streamWriter.Value;

    /// <summary>Opens a scope for the duration of one streaming chat turn; restores the previous writer (normally none) on dispose.</summary>
    public static IDisposable StreamScope(ChannelWriter<string> writer)
    {
        var previous = _streamWriter.Value;
        _streamWriter.Value = writer;
        return new Disposable(() => _streamWriter.Value = previous);
    }

    /// <summary>
    /// Guardrail detections recorded during the current request — the .NET
    /// analog of Python's <c>current_guardrail_flags</c> ContextVar
    /// (<c>shared/guardrails/flags.py</c>). Written by the injection-
    /// detection and output-moderation pipeline stages
    /// (<c>Shared/Agents/SpecialistPipeline.cs</c>); read by anything that
    /// needs to know whether this request's turn tripped a guardrail
    /// (currently: this repo's own tests — .NET has no eval harness yet to
    /// assert on this the way Python's does).
    /// </summary>
    public static IReadOnlyDictionary<string, bool> CurrentGuardrailFlags =>
        (IReadOnlyDictionary<string, bool>?)_guardrailFlags.Value ?? EmptyGuardrailFlags;

    private static readonly IReadOnlyDictionary<string, bool> EmptyGuardrailFlags = new Dictionary<string, bool>();

    /// <summary>Records a guardrail flag for the current request. No-op outside a <see cref="Scope"/>.</summary>
    public static void SetGuardrailFlag(string key, bool value) => (_guardrailFlags.Value ??= new())[key] = value;

    /// <summary>
    /// Agentic-timeline steps recorded during the current request — the .NET
    /// analog of Python's <c>current_steps</c> ContextVar
    /// (<c>shared/agent_observability.py</c>). One entry per tool call, in
    /// call order, appended by <c>SpecialistPipeline</c>'s step-recording
    /// stage. A specialist process tags its own entries with
    /// <c>Agent: null</c> (it only knows about itself); the orchestrator's
    /// <c>A2AClient</c> fills in <see cref="ExecutionStep.Agent"/> when it
    /// merges a specialist's returned steps into its own list — matching
    /// Python's "specialists record un-tagged, the orchestrator tags them on
    /// the way back" split described in <c>agent_observability.py</c>'s
    /// module docstring.
    /// </summary>
    public static IReadOnlyList<ExecutionStep> CurrentSteps =>
        (IReadOnlyList<ExecutionStep>?)_steps.Value ?? Array.Empty<ExecutionStep>();

    /// <summary>Appends one step to the current request. No-op outside a <see cref="Scope"/>.</summary>
    public static void RecordStep(ExecutionStep step) => _steps.Value?.Add(step);

    public static IDisposable Scope(string email, string role, string sessionId, IReadOnlyList<HistoryEntry>? history = null)
    {
        var previous = (
            Email: _userEmail.Value,
            Role: _userRole.Value,
            Session: _sessionId.Value,
            History: _history.Value,
            InvokedAgents: _invokedAgents.Value,
            GuardrailFlags: _guardrailFlags.Value,
            Steps: _steps.Value
        );
        _userEmail.Value = email;
        _userRole.Value = role;
        _sessionId.Value = sessionId;
        _history.Value = history ?? Array.Empty<HistoryEntry>();
        _invokedAgents.Value = new List<string>();
        _guardrailFlags.Value = new Dictionary<string, bool>();
        _steps.Value = new List<ExecutionStep>();
        return new Disposable(() =>
        {
            _userEmail.Value = previous.Email;
            _userRole.Value = previous.Role;
            _sessionId.Value = previous.Session;
            _history.Value = previous.History;
            _invokedAgents.Value = previous.InvokedAgents;
            _guardrailFlags.Value = previous.GuardrailFlags;
            _steps.Value = previous.Steps;
        });
    }

    private sealed class Disposable(Action dispose) : IDisposable
    {
        private readonly Action _dispose = dispose;

        public void Dispose() => _dispose();
    }
}

/// <summary>One entry in the forwarded conversation history (A2A payload shape).</summary>
public sealed record HistoryEntry(string Role, string Content);

/// <summary>
/// One agentic-timeline step — the .NET twin of Python's step dict
/// (<c>shared/agent_observability.py::StepRecorderMiddleware</c>). <c>Agent</c>
/// is null when a specialist records its own step (it doesn't know its own
/// name in this context) and filled in by the orchestrator's <c>A2AClient</c>
/// when merging a specialist's returned steps into its own timeline.
/// </summary>
public sealed record ExecutionStep(
    string ToolName,
    object? ToolInput,
    object? ToolOutput,
    string Status,
    int DurationMs,
    string? Agent = null
);
