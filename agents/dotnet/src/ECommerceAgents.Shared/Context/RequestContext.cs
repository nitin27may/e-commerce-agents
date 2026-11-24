using System.Runtime.CompilerServices;
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
    // Opaque id for one request/turn, stamped on model calls as x-run-id (Agents/GatewayHeaders.cs). Fresh per Scope;
    // not the session id and carries no identity. Python's current_run_id.
    private static readonly AsyncLocal<string?> _runId = new();
    private static readonly AsyncLocal<IReadOnlyList<HistoryEntry>?> _history = new();
    private static readonly AsyncLocal<List<string>?> _invokedAgents = new();
    private static readonly AsyncLocal<ChannelWriter<StreamFrame>?> _streamWriter = new();
    private static readonly AsyncLocal<HashSet<int>?> _deliveredSteps = new();
    private static readonly AsyncLocal<Dictionary<string, bool>?> _guardrailFlags = new();
    private static readonly AsyncLocal<List<ExecutionStep>?> _steps = new();
    // Running LLM spend for the current agent run, in USD. The .NET twin of
    // Python's current_run_cost_usd ContextVar (issue #30).
    private static readonly AsyncLocal<StrongBox<double>?> _runCostUsd = new();
    // Input + output tokens this run has used (the token ceiling); boxed for the same reason.
    private static readonly AsyncLocal<StrongBox<long>?> _runTokens = new();
    // Per-turn LLM token usage by agent (Python's current_run_usage). Mutable
    // object in an AsyncLocal, like _runCostUsd above, for the same reason.
    private static readonly AsyncLocal<RunUsage?> _runUsage = new();
    // Set when a specialist reported a replay miss (see ReplayMiss). A box for the reason _runCostUsd is one: the tool
    // call that records it runs in a task the function-invocation loop spawns, whose AsyncLocal writes the caller never sees.
    private static readonly AsyncLocal<StrongBox<bool>?> _replayMiss = new();
    // Sentences a gated tool call must put in front of the user word for word (a precheck refusal, the pending-approval
    // notice, a fail-closed reply). Same box-in-a-scope shape as _replayMiss: the tool call runs in a task whose AsyncLocal
    // writes the caller never sees, so the list itself is shared and guarded by its own lock.
    private static readonly AsyncLocal<List<string>?> _gateNotices = new();
    // The per-request grounding ledger: one mutable object shared by reference, so a tool call that records into it
    // from a task the function-invocation loop spawned is still seen by the code that verifies the answer.
    private static readonly AsyncLocal<Grounding.GroundingLedger?> _groundingLedger = new();
    // What this request was sent as: the fallback for the customer confirmation when there is no stored conversation to read the
    // user's own words from. A box for the reason _replayMiss is one (the streaming host's iterator drops AsyncLocal writes).
    private static readonly AsyncLocal<StrongBox<string?>?> _turnMessage = new();

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

    /// <summary>The current request's gateway run id; empty outside a <see cref="Scope"/>.</summary>
    public static string CurrentRunId => _runId.Value ?? string.Empty;

    /// <summary>Running estimated LLM spend for this agent run, in USD.</summary>
    public static double CurrentRunCostUsd => _runCostUsd.Value?.Value ?? 0.0;

    /// <summary>Starts a fresh spend tally. Called once per agent run.</summary>
    public static void ResetRunCost()
    {
        _runCostUsd.Value = new StrongBox<double>(0.0);
        _runTokens.Value = new StrongBox<long>(0);
    }

    /// <summary>Input + output tokens recorded so far in this run (counted even for an unpriced model).</summary>
    public static long CurrentRunTokens => _runTokens.Value?.Value ?? 0;

    /// <summary>Adds to the run's token tally and returns the new total.</summary>
    public static long AddRunTokens(long tokens)
    {
        var box = _runTokens.Value;
        if (box is null)
        {
            box = new StrongBox<long>(0);
            _runTokens.Value = box;
        }
        box.Value += Math.Max(tokens, 0);
        return box.Value;
    }

    /// <summary>
    /// Adds to the run's spend and returns the new total. Mutates a boxed
    /// value rather than reassigning the AsyncLocal, so an increment made
    /// deeper in the call tree is visible to the caller that started the run —
    /// reassignment would only be seen by that branch and below.
    /// </summary>
    public static double AddRunCost(double usd)
    {
        var box = _runCostUsd.Value;
        if (box is null)
        {
            box = new StrongBox<double>(0.0);
            _runCostUsd.Value = box;
        }
        box.Value += usd;
        return box.Value;
    }

    /// <summary>
    /// This turn's token usage by agent. Never null: outside a <see cref="Scope"/>
    /// it is a throwaway accumulator, so a stray read cannot throw.
    /// </summary>
    public static RunUsage CurrentRunUsage => _runUsage.Value ?? new RunUsage();

    /// <summary>Adds <paramref name="usage"/> to this turn's tally under <paramref name="agent"/>. No-op outside a <see cref="Scope"/>.</summary>
    public static void RecordUsage(string agent, SpecialistUsage? usage) => _runUsage.Value?.Add(agent, usage);

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
    /// <para>
    /// Carries a <see cref="StreamFrame"/> rather than a bare string because a
    /// delta is no longer the only thing worth forwarding mid-turn: a specialist's
    /// tool steps travel the same way, and a channel of plain strings could only
    /// ever produce one kind of SSE frame.
    /// </para>
    /// <c>null</c> outside a streaming turn (blocking <c>/api/chat</c>, or any
    /// caller that never opened a scope) — every write site must treat that as a
    /// safe no-op, matching <see cref="RecordInvokedAgent"/>'s own null-safety.
    /// </summary>
    public static ChannelWriter<StreamFrame>? CurrentStreamWriter => _streamWriter.Value;

    /// <summary>Opens a scope for the duration of one streaming chat turn; restores the previous writer (normally none) on dispose.</summary>
    public static IDisposable StreamScope(ChannelWriter<StreamFrame> writer)
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

    /// <summary>
    /// Marks the most recently recorded step as already sent to the browser.
    /// </summary>
    /// <remarks>
    /// <para>
    /// A specialist's steps are forwarded live by <c>A2AClient.MergeReturnedSteps</c>
    /// as they arrive; the orchestrator's own steps are not. <c>ChatRoutes</c> then
    /// emits whatever is left at the end of the run, and needs to know which is which
    /// or it sends the specialist's rows twice.
    /// </para>
    /// <para>
    /// Tracked by index rather than by a count of delivered steps, because delivered
    /// steps are <em>not</em> a prefix of the list. Two specialist calls interleave as
    /// [spec1 (sent), orchestrator (not), spec2 (sent), orchestrator (not)], so
    /// "skip the first N" would drop the wrong rows. Tracked here rather than as a
    /// field on <see cref="ExecutionStep"/> so a delivery detail never reaches the
    /// persisted timeline — the equivalent of Python popping its <c>_live</c> marker
    /// before the metadata is written.
    /// </para>
    /// </remarks>
    public static void MarkLastStepDelivered()
    {
        var steps = _steps.Value;
        if (steps is { Count: > 0 })
        {
            (_deliveredSteps.Value ??= new HashSet<int>()).Add(steps.Count - 1);
        }
    }

    /// <summary>Whether the step at <paramref name="index"/> has already been streamed.</summary>
    public static bool IsStepDelivered(int index) => _deliveredSteps.Value?.Contains(index) ?? false;

    /// <summary>
    /// Records that a specialist's model call had no recording (<c>LLM_PROVIDER=replay</c>). The tool loop swallows an
    /// exception a tool raises (the model would read it as a result and call again, which is exactly what must not
    /// happen), so the tool records the miss here, ends the loop, and the chat route reads it back. Twin of
    /// Python's <c>shared/replay_miss.py::begin_miss_tracking</c> / <c>raise_if_specialist_missed</c>.
    /// </summary>
    public static void RecordReplayMiss()
    {
        if (_replayMiss.Value is { } box)
        {
            box.Value = true;
        }
    }

    /// <summary>Whether a specialist's replay miss ended this request's tool loop.</summary>
    public static bool ReplayMissRecorded => _replayMiss.Value?.Value ?? false;

    /// <summary>
    /// Whether a gate notice sink is active for this request. When it is not (a caller outside a <see cref="Scope"/>), the
    /// pipeline cannot promise the sentence reaches the user and leaves the tool result for the model to relay.
    /// </summary>
    public static bool GateNoticesActive => _gateNotices.Value is not null;

    /// <summary>Starts a sink if none is active, for a host entered without a <see cref="Scope"/>; the returned handle restores the previous one.</summary>
    public static IDisposable EnsureGateNotices()
    {
        if (_gateNotices.Value is not null)
        {
            return new Disposable(() => { });
        }

        _gateNotices.Value = new List<string>();
        return new Disposable(() => _gateNotices.Value = null);
    }

    /// <summary>Records a sentence the user must read. Returns false (and records nothing) when no sink is active.</summary>
    public static bool RecordGateNotice(string sentence)
    {
        if (_gateNotices.Value is not { } list)
        {
            return false;
        }

        lock (list)
        {
            list.Add(sentence);
        }

        return true;
    }

    /// <summary>
    /// The recorded sentences in call order, deduplicated, joined with a single space (Python's <c>park_pending_approvals</c>
    /// return value); empty when none. Drains the sink so a second read does not repeat them.
    /// </summary>
    public static string TakeGateNotice()
    {
        if (_gateNotices.Value is not { } list)
        {
            return string.Empty;
        }

        lock (list)
        {
            var joined = string.Join(" ", list.Distinct(StringComparer.Ordinal));
            list.Clear();
            return joined;
        }
    }

    /// <summary>
    /// The facts this request's tool calls returned (and the values the caller typed), or null outside a
    /// <see cref="Scope"/>. The .NET twin of Python's <c>current_grounding_ledger</c> ContextVar
    /// (<c>shared/grounding/ledger.py</c>): written by <c>SpecialistPipeline</c>'s grounding stage, read by the
    /// verifier. Every scope starts with an empty one.
    /// </summary>
    public static Grounding.GroundingLedger? CurrentGroundingLedger => _groundingLedger.Value;

    /// <summary>The message this request was sent as, or null (python's <c>user_message</c> argument to <c>park_pending_approvals</c>).</summary>
    public static string? CurrentTurnMessage => _turnMessage.Value?.Value;

    /// <summary>Records the message this request was sent as. A no-op outside a <see cref="Scope"/>.</summary>
    public static void NoteTurnMessage(string? message)
    {
        if (_turnMessage.Value is { } box)
        {
            box.Value = message;
        }
    }

    public static IDisposable Scope(string email, string role, string sessionId, IReadOnlyList<HistoryEntry>? history = null)
    {
        var previous = (
            Email: _userEmail.Value,
            Role: _userRole.Value,
            Session: _sessionId.Value,
            RunId: _runId.Value,
            History: _history.Value,
            InvokedAgents: _invokedAgents.Value,
            GuardrailFlags: _guardrailFlags.Value,
            Steps: _steps.Value,
            DeliveredSteps: _deliveredSteps.Value,
            RunUsage: _runUsage.Value,
            RunCost: _runCostUsd.Value,
            RunTokens: _runTokens.Value,
            ReplayMiss: _replayMiss.Value,
            GateNotices: _gateNotices.Value,
            GroundingLedger: _groundingLedger.Value,
            TurnMessage: _turnMessage.Value
        );
        _userEmail.Value = email;
        _userRole.Value = role;
        _sessionId.Value = sessionId;
        _runId.Value = Guid.NewGuid().ToString("N")[..16];
        _history.Value = history ?? Array.Empty<HistoryEntry>();
        _invokedAgents.Value = new List<string>();
        _guardrailFlags.Value = new Dictionary<string, bool>();
        _steps.Value = new List<ExecutionStep>();
        _deliveredSteps.Value = new HashSet<int>();
        _runUsage.Value = new RunUsage();
        _replayMiss.Value = new StrongBox<bool>(false);
        _gateNotices.Value = new List<string>();
        _groundingLedger.Value = new Grounding.GroundingLedger();
        _turnMessage.Value = new StrongBox<string?>(null);
        // A fresh spend tally per scope. Nothing ever called ResetRunCost, and AddRunCost's lazy
        // creation happens inside whatever async frame first prices a turn — a change an
        // AsyncLocal does not carry back to the caller — so the running total, and with it the
        // per-run ceiling, was never visible to the code that checks it.
        _runCostUsd.Value = new StrongBox<double>(0.0);
        _runTokens.Value = new StrongBox<long>(0);
        return new Disposable(() =>
        {
            _userEmail.Value = previous.Email;
            _userRole.Value = previous.Role;
            _sessionId.Value = previous.Session;
            _runId.Value = previous.RunId;
            _history.Value = previous.History;
            _invokedAgents.Value = previous.InvokedAgents;
            _guardrailFlags.Value = previous.GuardrailFlags;
            _steps.Value = previous.Steps;
            _deliveredSteps.Value = previous.DeliveredSteps;
            _runUsage.Value = previous.RunUsage;
            _runCostUsd.Value = previous.RunCost;
            _runTokens.Value = previous.RunTokens;
            _replayMiss.Value = previous.ReplayMiss;
            _gateNotices.Value = previous.GateNotices;
            _groundingLedger.Value = previous.GroundingLedger;
            _turnMessage.Value = previous.TurnMessage;
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
