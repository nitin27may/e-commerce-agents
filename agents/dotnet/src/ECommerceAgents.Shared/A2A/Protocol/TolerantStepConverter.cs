using System.Text.Json;
using System.Text.Json.Serialization;
using ECommerceAgents.Shared.Context;

namespace ECommerceAgents.Shared.A2A.Protocol;

/// <summary>
/// Reads a specialist's step from the legacy wire whichever stack wrote it. A .NET specialist serialises
/// <see cref="ExecutionStep"/> as <c>toolName</c> / <c>ToolName</c>; a Python specialist writes its step dict as
/// <c>tool_name</c> / <c>tool_input</c> / <c>tool_output</c> / <c>duration_ms</c>. The orchestrator's reader matched
/// only the first, so a .NET orchestrator over the legacy wire silently dropped every step of a Python specialist
/// (found by the cross-process interop check). Property names are compared ignoring case and underscores.
/// </summary>
public sealed class TolerantStepConverter : JsonConverter<ExecutionStep>
{
    public override ExecutionStep? Read(ref Utf8JsonReader reader, Type typeToConvert, JsonSerializerOptions options)
    {
        using var doc = JsonDocument.ParseValue(ref reader);
        if (doc.RootElement.ValueKind != JsonValueKind.Object)
        {
            return null;
        }

        var fields = new Dictionary<string, JsonElement>(StringComparer.Ordinal);
        foreach (var property in doc.RootElement.EnumerateObject())
        {
            fields[property.Name.Replace("_", string.Empty, StringComparison.Ordinal).ToLowerInvariant()] = property.Value.Clone();
        }

        string Text(string key) => fields.TryGetValue(key, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() ?? string.Empty : string.Empty;

        object? Any(string key) => fields.TryGetValue(key, out var v) && v.ValueKind != JsonValueKind.Null ? v : null;

        var duration = fields.TryGetValue("durationms", out var d) && d.ValueKind == JsonValueKind.Number && d.TryGetDouble(out var ms)
            ? (int)Math.Round(ms)
            : 0;
        var agent = Text("agent");
        return new ExecutionStep(
            Text("toolname"), Any("toolinput"), Any("tooloutput"), Text("status"), duration, agent.Length == 0 ? null : agent);
    }

    public override void Write(Utf8JsonWriter writer, ExecutionStep value, JsonSerializerOptions options) =>
        JsonSerializer.Serialize(writer, value, new JsonSerializerOptions(JsonSerializerDefaults.Web));
}
