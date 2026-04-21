using YamlDotNet.Serialization;
using YamlDotNet.Serialization.NamingConventions;

namespace ECommerceAgents.Shared.Prompts;

/// <summary>
/// Loads agent system prompts from <c>config/prompts/*.yaml</c>. Mirrors
/// the composition rules in Python's <c>shared/prompt_loader.py</c> so
/// both backends read the same YAML files and produce identical system
/// prompts for a given agent + role.
/// </summary>
/// <remarks>
/// The YAML schema is:
/// <code>
/// name: orchestrator
/// system_prompt:
///   base: |
///     You are the Customer Support orchestrator...
///   role_instructions:
///     customer: |
///       ...
///     admin: |
///       ...
///   schema_refs: [users, orders]
///   tool_example_refs: [call_specialist_agent]
/// </code>
/// Shared fragments live in <c>_shared/</c>:
/// <list type="bullet">
/// <item><c>grounding-rules.yaml</c> with top-level <c>rules:</c></item>
/// <item><c>schema-context.yaml</c> — arbitrary keyed sections matched by <c>schema_refs</c></item>
/// <item><c>tool-examples.yaml</c> — arbitrary keyed sections matched by <c>tool_example_refs</c></item>
/// </list>
/// </remarks>
public sealed class PromptLoader
{
    private readonly string _promptsRoot;
    private readonly IDeserializer _yaml = new DeserializerBuilder()
        .WithNamingConvention(UnderscoredNamingConvention.Instance)
        .IgnoreUnmatchedProperties()
        .Build();

    public PromptLoader(string promptsRoot)
    {
        if (string.IsNullOrWhiteSpace(promptsRoot))
        {
            throw new ArgumentException("promptsRoot is required", nameof(promptsRoot));
        }

        _promptsRoot = promptsRoot;
    }

    /// <summary>Compose the full system prompt for <paramref name="agentName"/> in <paramref name="userRole"/>.</summary>
    public string Load(string agentName, string? userRole = null)
    {
        var role = string.IsNullOrWhiteSpace(userRole) ? "customer" : userRole;
        var agentPath = Path.Combine(_promptsRoot, $"{agentName}.yaml");
        if (!File.Exists(agentPath))
        {
            return string.Empty;
        }

        var config = _yaml.Deserialize<Dictionary<string, object>>(File.ReadAllText(agentPath))
                     ?? new Dictionary<string, object>();
        var systemPrompt = ExtractDictionary(config, "system_prompt");

        var parts = new List<string>();

        // 1. Base prompt.
        var basePrompt = ExtractString(systemPrompt, "base");
        if (!string.IsNullOrWhiteSpace(basePrompt))
        {
            parts.Add(basePrompt.Trim());
        }

        // 2. Grounding rules — always included.
        var grounding = LoadSharedFile("grounding-rules.yaml");
        var rules = ExtractString(grounding, "rules");
        if (!string.IsNullOrWhiteSpace(rules))
        {
            parts.Add(rules.Trim());
        }

        // 3. Role-specific instructions (falls back to customer).
        var roleInstructions = ExtractDictionary(systemPrompt, "role_instructions");
        var roleText = ExtractString(roleInstructions, role) ?? ExtractString(roleInstructions, "customer");
        if (!string.IsNullOrWhiteSpace(roleText))
        {
            parts.Add($"## Your Role Context\n{roleText.Trim()}");
        }

        // 4. Schema context.
        var schemaData = LoadSharedFile("schema-context.yaml");
        foreach (var key in ExtractStringList(systemPrompt, "schema_refs"))
        {
            var section = ExtractString(schemaData, key);
            if (!string.IsNullOrWhiteSpace(section))
            {
                parts.Add(section.Trim());
            }
        }

        // 5. Tool examples.
        var toolData = LoadSharedFile("tool-examples.yaml");
        foreach (var key in ExtractStringList(systemPrompt, "tool_example_refs"))
        {
            var section = ExtractString(toolData, key);
            if (!string.IsNullOrWhiteSpace(section))
            {
                parts.Add(section.Trim());
            }
        }

        return string.Join("\n\n", parts);
    }

    private Dictionary<string, object> LoadSharedFile(string filename)
    {
        var path = Path.Combine(_promptsRoot, "_shared", filename);
        if (!File.Exists(path))
        {
            return new Dictionary<string, object>();
        }

        return _yaml.Deserialize<Dictionary<string, object>>(File.ReadAllText(path))
               ?? new Dictionary<string, object>();
    }

    private static Dictionary<string, object> ExtractDictionary(IDictionary<string, object> source, string key)
    {
        if (source.TryGetValue(key, out var value) && value is Dictionary<object, object> mapping)
        {
            return mapping.Where(kv => kv.Key is string)
                          .ToDictionary(kv => (string)kv.Key, kv => kv.Value);
        }
        return new Dictionary<string, object>();
    }

    private static string? ExtractString(IDictionary<string, object> source, string key)
    {
        if (!source.TryGetValue(key, out var value) || value is null)
        {
            return null;
        }

        return value is string s ? s : value.ToString();
    }

    private static List<string> ExtractStringList(IDictionary<string, object> source, string key)
    {
        if (!source.TryGetValue(key, out var value) || value is not List<object> list)
        {
            return new List<string>();
        }

        return list.Select(item => item?.ToString() ?? string.Empty)
                   .Where(s => !string.IsNullOrWhiteSpace(s))
                   .ToList();
    }
}
