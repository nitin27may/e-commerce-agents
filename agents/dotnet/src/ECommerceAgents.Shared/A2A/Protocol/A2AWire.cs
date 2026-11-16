using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;
using ECommerceAgents.Shared.Context;

namespace ECommerceAgents.Shared.A2A.Protocol;

/// <summary>
/// The A2A 1.0 wire contract this repo layers on the official SDK, byte-for-byte the one the Python
/// specialists serve (<c>shared/a2a_server.py</c>). A .NET specialist and a Python specialist are
/// interchangeable behind either orchestrator, so every constant here has a twin there.
/// </summary>
public static class A2AWire
{
    /// <summary>Where the JSON-RPC interface is mounted. The legacy routes keep the root.</summary>
    public const string RpcPath = "/a2a/v1";

    /// <summary>The A2A spec version served and spoken (JSON-RPC binding only).</summary>
    public const string ProtocolVersion = "1.0";

    public const string AgentVersion = "1.0";

    private const string ExtBase = "https://github.com/nitin27may/e-commerce-agents/a2a/ext";

    /// <summary>Status-update metadata: tool-call steps. Shape <c>{steps: [...], offset: n}</c>.</summary>
    public const string StepsExtensionUri = ExtBase + "/steps/v1";

    /// <summary>Terminal status-update metadata: token accounting (the legacy <c>usage</c> shape).</summary>
    public const string UsageExtensionUri = ExtBase + "/usage/v1";

    /// <summary>Failed-task metadata: <c>{error: "replay_miss"}</c> (<c>LLM_PROVIDER=replay</c> only).</summary>
    public const string ReplayMissExtensionUri = ExtBase + "/replay-miss/v1";

    public static readonly IReadOnlyList<string> AllExtensionUris =
        [StepsExtensionUri, UsageExtensionUri, ReplayMissExtensionUri];

    /// <summary>Extension descriptions, the same text the Python card carries.</summary>
    public static string DescriptionOf(string uri) => uri switch
    {
        StepsExtensionUri =>
            "Tool-call timeline. Each status update's metadata[uri] is {steps: [...], offset: n}: the steps "
            + "starting at index n. The terminal update repeats all steps with offset 0.",
        UsageExtensionUri =>
            "Token accounting for the run. The terminal status update's metadata[uri] is "
            + "{input_token_count, output_token_count, cached_input_token_count, model}; it is never part of "
            + "the answer text.",
        ReplayMissExtensionUri =>
            "Set on a failed task whose model call had no recorded response (LLM_PROVIDER=replay only). "
            + "metadata[uri] is {error: 'replay_miss'}.",
        _ => string.Empty,
    };

    /// <summary>The text on a task that ended in an unexpected error. Fixed: an exception's own text never travels.</summary>
    public const string FailureText = "Agent processing failed";

    /// <summary>What a caller is told when a specialist already runs as many tasks as it will.</summary>
    public const string TooManyTasksText = "Too many active tasks; retry later";

    public const string NoMessageText = "No message provided";

    /// <summary>The fixed text for an exception outside the executor (a failing store, a handler bug).</summary>
    public const string InternalErrorText = "Internal error";

    /// <summary>The replay-miss marker body (<c>shared/replay_miss.py::REPLAY_MISS_BODY</c>).</summary>
    public const string ReplayMissError = "replay_miss";

    /// <summary>Failed-task prefix the orchestrator never shows a user; used to detect a miss on the wire.</summary>
    public const string StateFailed = "failed";

    /// <summary>
    /// The conversation id a specialist rehydrates history from: the message's <c>contextId</c> when the
    /// <em>caller</em> supplied one that is a UUID, else the <c>X-Session-Id</c> header the auth
    /// middleware stamped (kept so a caller that only sends the header still works). A server-invented
    /// context id (the SDK mints a GUID when the caller sent none, indistinguishable from a session id by
    /// shape) and a malformed one yield <c>""</c>: rehydration is then skipped, never a non-UUID cast.
    /// </summary>
    public static string SessionIdFromContext(string? contextId, bool clientProvided, string? headerSessionId)
    {
        foreach (var candidate in new[] { clientProvided ? contextId : null, headerSessionId })
        {
            if (!string.IsNullOrEmpty(candidate) && Guid.TryParse(candidate, out var parsed))
            {
                return parsed.ToString("D");
            }
        }

        return string.Empty;
    }

    /// <summary>The wire step object (Python's step dict: snake_case keys), in both directions.</summary>
    public sealed record WireStep(
        [property: JsonPropertyName("tool_name")] string ToolName,
        [property: JsonPropertyName("tool_input")] object? ToolInput,
        [property: JsonPropertyName("tool_output")] object? ToolOutput,
        [property: JsonPropertyName("status")] string Status,
        [property: JsonPropertyName("duration_ms")] double DurationMs,
        [property: JsonPropertyName("agent")] string? Agent = null)
    {
        public static WireStep From(ExecutionStep step, string agent) =>
            new(step.ToolName, step.ToolInput, step.ToolOutput, step.Status, step.DurationMs, agent);

        public ExecutionStep ToExecutionStep(string? fallbackAgent = null) =>
            new(ToolName, ToolInput, ToolOutput, Status, (int)Math.Round(DurationMs), Agent ?? fallbackAgent);
    }

    /// <summary>JSON for metadata and steps: plain (snake_case names are explicit on the records).</summary>
    public static readonly JsonSerializerOptions WireJson = new(JsonSerializerDefaults.Web)
    {
        DefaultIgnoreCondition = JsonIgnoreCondition.Never,
    };

    public static JsonElement ToElement(object value) =>
        JsonSerializer.SerializeToElement(value, WireJson);

    /// <summary>A <c>{steps, offset}</c> extension payload.</summary>
    public static JsonElement StepsPayload(IReadOnlyList<WireStep> steps, int offset) =>
        ToElement(new StepsPayloadBody(steps, offset));

    public sealed record StepsPayloadBody(
        [property: JsonPropertyName("steps")] IReadOnlyList<WireStep> Steps,
        [property: JsonPropertyName("offset")] int Offset);

    /// <summary>
    /// Folds a steps payload into <paramref name="known"/>, idempotent by index (so the terminal update, which
    /// repeats every step at offset 0, adds nothing a streaming caller has already seen); returns what was new.
    /// </summary>
    public static List<WireStep> MergeSteps(List<WireStep> known, JsonElement payload)
    {
        var fresh = new List<WireStep>();
        if (payload.ValueKind != JsonValueKind.Object)
        {
            return fresh;
        }

        // A protobuf Struct (the Python SDK's metadata) writes every number as a float: 0.0, not 0.
        var offset = payload.TryGetProperty("offset", out var o) && Whole(o) is { } n ? (int)n : 0;
        if (!payload.TryGetProperty("steps", out var list) || list.ValueKind != JsonValueKind.Array)
        {
            return fresh;
        }

        var i = 0;
        foreach (var raw in list.EnumerateArray())
        {
            var index = offset + i++;
            if (index < known.Count || raw.ValueKind != JsonValueKind.Object)
            {
                continue;
            }

            WireStep? step;
            try
            {
                step = raw.Deserialize<WireStep>(WireJson);
            }
            catch (JsonException)
            {
                continue;
            }

            if (step is null)
            {
                continue;
            }

            known.Add(step);
            fresh.Add(step);
        }

        return fresh;
    }

    /// <summary>Parses a usage payload; <c>null</c> when it is not the expected object.</summary>
    public static SpecialistUsage? ParseUsage(JsonElement element)
    {
        if (element.ValueKind != JsonValueKind.Object)
        {
            return null;
        }

        // Read field by field: the Python SDK's metadata is a protobuf Struct, whose numbers arrive as 5.0.
        long Count(string name) => element.TryGetProperty(name, out var v) && Whole(v) is { } n ? n : 0;

        var model = element.TryGetProperty("model", out var m) && m.ValueKind == JsonValueKind.String ? m.GetString() ?? string.Empty : string.Empty;
        var usage = new SpecialistUsage(Count("input_token_count"), Count("output_token_count"), Count("cached_input_token_count"), model);
        return usage.InputTokenCount == 0 && usage.OutputTokenCount == 0 ? null : usage;
    }

    /// <summary>A JSON number as a whole count (<c>5</c> and <c>5.0</c> both give 5); <c>null</c> for anything else.</summary>
    private static long? Whole(JsonElement element) =>
        element.ValueKind == JsonValueKind.Number && element.TryGetDouble(out var d) && d == Math.Floor(d) && Math.Abs(d) < long.MaxValue
            ? (long)d
            : null;

    /// <summary>A metadata dictionary (the shape the SDK's events carry).</summary>
    public static Dictionary<string, JsonElement> Metadata(params (string Key, JsonElement Value)[] entries)
    {
        var map = new Dictionary<string, JsonElement>(StringComparer.Ordinal);
        foreach (var (key, value) in entries)
        {
            map[key] = value;
        }

        return map;
    }

    internal static JsonNode? Node(object value) => JsonSerializer.SerializeToNode(value, WireJson);
}
