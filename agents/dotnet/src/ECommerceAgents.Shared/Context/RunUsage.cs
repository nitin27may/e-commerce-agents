using Microsoft.Extensions.AI;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace ECommerceAgents.Shared.Context;

/// <summary>
/// One LLM-usage report as it travels between a specialist and the
/// orchestrator — the .NET twin of Python's <c>shared/run_usage.py::to_wire_usage</c>.
/// </summary>
/// <remarks>
/// The wire shape is identical on both stacks and pinned by one literal in a test
/// on each side (<c>SpecialistUsageTests.WireJson</c> here,
/// <c>WIRE_USAGE_JSON</c> in <c>tests/test_specialist_usage.py</c>):
/// <code>{"input_token_count":120,"output_token_count":30,"cached_input_token_count":100,"model":"gpt-4.1"}</code>
/// It rides in its own <c>usage</c> field on <c>/message:send</c> and in a
/// terminal <c>event: usage</c> frame on <c>/message:stream</c>. It never enters
/// the text the orchestrator's model reads, nor the browser's SSE stream.
/// </remarks>
public sealed record SpecialistUsage(
    [property: JsonPropertyName("input_token_count")] long InputTokenCount,
    [property: JsonPropertyName("output_token_count")] long OutputTokenCount,
    [property: JsonPropertyName("cached_input_token_count")] long CachedInputTokenCount,
    [property: JsonPropertyName("model")] string Model
)
{
    /// <summary>The compact JSON a frame or response field carries.</summary>
    public string ToWireJson() => JsonSerializer.Serialize(this);

    /// <summary>MEAI usage → wire shape; <c>null</c> when it reports no tokens at all.</summary>
    public static SpecialistUsage? From(UsageDetails? details, string model)
    {
        if (details is null)
        {
            return null;
        }

        var input = details.InputTokenCount ?? 0;
        var output = details.OutputTokenCount ?? 0;
        if (input == 0 && output == 0)
        {
            return null;
        }

        return new SpecialistUsage(input, output, details.CachedInputTokenCount ?? 0, model ?? string.Empty);
    }
}

/// <summary>
/// Per-turn usage accumulator, summed per agent — the .NET twin of Python's
/// <c>RunUsage</c>. Held in a <see cref="RequestContext"/> AsyncLocal as a
/// mutable object, so an <see cref="Add"/> made deep in the call tree is seen by
/// the route that reads the total when it writes <c>usage_logs</c>.
/// </summary>
public sealed class RunUsage
{
    /// <summary>Key a process uses for its own LLM calls (a specialist knows no name for itself).</summary>
    public const string SelfKey = "self";

    private readonly object _gate = new();
    private readonly Dictionary<string, SpecialistUsage> _byAgent = new();

    public void Add(string agent, SpecialistUsage? usage)
    {
        if (usage is null)
        {
            return;
        }

        lock (_gate)
        {
            _byAgent[agent] = _byAgent.TryGetValue(agent, out var existing)
                ? new SpecialistUsage(
                    existing.InputTokenCount + usage.InputTokenCount,
                    existing.OutputTokenCount + usage.OutputTokenCount,
                    existing.CachedInputTokenCount + usage.CachedInputTokenCount,
                    usage.Model)
                : usage;
        }
    }

    public IReadOnlyDictionary<string, SpecialistUsage> ByAgent
    {
        get
        {
            lock (_gate)
            {
                return new Dictionary<string, SpecialistUsage>(_byAgent);
            }
        }
    }

    /// <summary>Everything recorded this turn, summed; <c>null</c> when nothing was.</summary>
    public SpecialistUsage? Total()
    {
        lock (_gate)
        {
            if (_byAgent.Count == 0)
            {
                return null;
            }

            return new SpecialistUsage(
                _byAgent.Values.Sum(u => u.InputTokenCount),
                _byAgent.Values.Sum(u => u.OutputTokenCount),
                _byAgent.Values.Sum(u => u.CachedInputTokenCount),
                _byAgent.Values.Last().Model);
        }
    }
}
