using ECommerceAgents.Shared.Orchestration;

namespace ECommerceAgents.Orchestrator.Modes;

/// <summary>Raised when a caller asks for a mode this backend doesn't have.</summary>
public sealed class UnknownModeException(string name, IEnumerable<string> available)
    : Exception($"Unknown orchestration mode: '{name}'. Available: {string.Join(", ", available)}.")
{
    public string RequestedMode { get; } = name;
    public IReadOnlyList<string> AvailableModes { get; } = available.ToList();
}

/// <summary>
/// The modes this backend can run — the .NET twin of Python's
/// <c>orchestrator/modes/__init__.py</c> registry.
/// </summary>
/// <remarks>
/// Before this existed, <c>ChatRoutes</c> held a single hardcoded agent call
/// and the frontend's <c>mode</c> field was discarded outright. Registering
/// modes here is what makes <c>GET /api/orchestration/modes</c>, the mode
/// switcher, the graph panel and mode comparison possible at all.
///
/// Both stacks now register the same five: <c>tool</c>,
/// <c>workflow:pre-purchase</c>, <c>workflow:return-replace</c>,
/// <c>handoff</c> and <c>group-chat</c>. The rule that shaped the earlier gap
/// still holds — a mode registered but unable to run is worse than one
/// honestly absent, and <c>ChatRoutes</c> rejects an unregistered mode with a
/// 400 naming what is available.
///
/// Magentic is in neither stack. It is unbuilt rather than a .NET gap, so it
/// is not tracked as one.
/// </remarks>
public sealed class ModeRegistry
{
    private readonly Dictionary<string, IOrchestrationMode> _modes;

    public const string DefaultMode = "tool";

    public ModeRegistry(IEnumerable<IOrchestrationMode> modes)
    {
        _modes = modes.ToDictionary(m => m.Name, StringComparer.OrdinalIgnoreCase);
    }

    public IReadOnlyCollection<IOrchestrationMode> All => _modes.Values;

    public bool Contains(string? name) =>
        !string.IsNullOrWhiteSpace(name) && _modes.ContainsKey(name);

    public IOrchestrationMode Get(string? name)
    {
        var resolved = string.IsNullOrWhiteSpace(name) ? DefaultMode : name;
        return _modes.TryGetValue(resolved, out var mode)
            ? mode
            : throw new UnknownModeException(resolved, _modes.Keys);
    }

    /// <summary>Shape consumed by <c>GET /api/orchestration/modes</c>.</summary>
    public IReadOnlyList<object> Describe() =>
        _modes.Values
            .OrderBy(m => m.Name, StringComparer.Ordinal)
            .Select(m => (object)new
            {
                name = m.Name,
                label = m.Label,
                description = m.Description,
                capabilities = new
                {
                    streams = m.Capabilities.Streams,
                    supports_hitl = m.Capabilities.SupportsHitl,
                    supports_checkpoints = m.Capabilities.SupportsCheckpoints,
                    is_graph = m.Capabilities.IsGraph,
                },
            })
            .ToList();
}
