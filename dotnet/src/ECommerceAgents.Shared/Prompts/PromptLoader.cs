using YamlDotNet.Serialization;
using YamlDotNet.Serialization.NamingConventions;

namespace ECommerceAgents.Shared.Prompts;

/// <summary>
/// Loads agent system prompts from <c>config/prompts/*.yaml</c>. Mirrors
/// Python's <c>shared/prompt_loader.py</c> composition rules so the .NET
/// backend can consume the same YAML files the Python backend uses —
/// zero duplication of prompt text.
/// </summary>
/// <remarks>
/// Prompts compose four sections:
/// <list type="bullet">
/// <item>agent base (required) — per-agent <c>base_prompt</c>.</item>
/// <item><c>_shared/grounding-rules.yaml</c> — shared DB/tool guardrails.</item>
/// <item><c>_shared/schema-context.yaml</c> — table shapes for DB-aware agents.</item>
/// <item><c>_shared/tool-examples.yaml</c> — few-shot tool-use snippets.</item>
/// </list>
/// Each agent YAML can declare per-role overrides under <c>roles:</c>.
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

    /// <summary>
    /// Compose the full system prompt for <paramref name="agentName"/>,
    /// optionally applying <paramref name="userRole"/> overrides.
    /// </summary>
    public string Load(string agentName, string? userRole = null)
    {
        var agentPath = Path.Combine(_promptsRoot, $"{agentName}.yaml");
        if (!File.Exists(agentPath))
        {
            throw new FileNotFoundException($"Prompt file not found: {agentPath}");
        }

        var agent = DeserializeAgentPrompt(File.ReadAllText(agentPath));
        var grounding = TryReadSharedFragment("grounding-rules.yaml");
        var schema = TryReadSharedFragment("schema-context.yaml");
        var examples = TryReadSharedFragment("tool-examples.yaml");

        var sections = new List<string>();
        if (!string.IsNullOrWhiteSpace(agent.BasePrompt))
        {
            sections.Add(agent.BasePrompt.Trim());
        }

        var roleOverride = ResolveRoleOverride(agent, userRole);
        if (!string.IsNullOrWhiteSpace(roleOverride))
        {
            sections.Add(roleOverride.Trim());
        }

        if (!string.IsNullOrWhiteSpace(grounding))
        {
            sections.Add(grounding.Trim());
        }

        if (!string.IsNullOrWhiteSpace(schema) && agent.IncludeSchema)
        {
            sections.Add(schema.Trim());
        }

        if (!string.IsNullOrWhiteSpace(examples) && agent.IncludeExamples)
        {
            sections.Add(examples.Trim());
        }

        return string.Join("\n\n", sections);
    }

    private string? TryReadSharedFragment(string filename)
    {
        var path = Path.Combine(_promptsRoot, "_shared", filename);
        if (!File.Exists(path))
        {
            return null;
        }

        var fragment = _yaml.Deserialize<SharedFragment>(File.ReadAllText(path));
        return fragment?.Content;
    }

    private AgentPromptFile DeserializeAgentPrompt(string yaml) =>
        _yaml.Deserialize<AgentPromptFile>(yaml)
        ?? throw new InvalidDataException("Agent prompt YAML deserialized to null");

    private static string? ResolveRoleOverride(AgentPromptFile file, string? role)
    {
        if (role is null || file.Roles is null)
        {
            return null;
        }

        return file.Roles.TryGetValue(role.ToLowerInvariant(), out var text) ? text : null;
    }

    private sealed class AgentPromptFile
    {
        public string BasePrompt { get; set; } = "";
        public bool IncludeSchema { get; set; } = true;
        public bool IncludeExamples { get; set; } = true;
        public Dictionary<string, string>? Roles { get; set; }
    }

    private sealed class SharedFragment
    {
        public string Content { get; set; } = "";
    }
}
