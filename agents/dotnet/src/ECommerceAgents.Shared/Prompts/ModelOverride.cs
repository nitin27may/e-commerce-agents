using System.Globalization;
using ECommerceAgents.Shared.Configuration;

namespace ECommerceAgents.Shared.Prompts;

/// <summary>
/// An agent's optional <c>model:</c> block from <c>config/prompts/&lt;agent&gt;.yaml</c> — the twin of
/// Python's <c>shared.prompt_loader.ModelOverride</c>. Every field may be null; null falls back to the
/// global settings. No shipped YAML file carries one.
/// </summary>
/// <remarks>
/// The block is configuration, not prompt text: <see cref="PromptLoader.Load"/> never reads it, so the
/// composed system prompt is byte-identical with or without it (replay fixtures are keyed by it).
/// </remarks>
public sealed record ModelOverride(string? Provider, string? Model, double? Temperature)
{
    /// <summary>Providers a <c>model:</c> block may name. Anthropic is .NET-only (see docs/parity-matrix.md).</summary>
    public static readonly string[] SupportedProviders = ["openai", "azure", "anthropic"];

    internal static readonly string[] AllowedKeys = ["provider", "model", "temperature"];

    /// <summary>
    /// Parses an already-deserialised <c>model:</c> value. Every error names <paramref name="source"/>
    /// (the file) and never echoes anything but configuration.
    /// </summary>
    internal static ModelOverride? Parse(object? raw, string source)
    {
        if (raw is null)
        {
            return null;
        }

        if (raw is not Dictionary<object, object> map)
        {
            throw new InvalidOperationException(
                $"{source}: 'model' must be a mapping with provider/model/temperature keys");
        }

        var unknown = map.Keys.Select(k => k?.ToString() ?? "").Where(k => !AllowedKeys.Contains(k)).Order().ToList();
        if (unknown.Count > 0)
        {
            throw new InvalidOperationException(
                $"{source}: unknown key(s) in 'model': {string.Join(", ", unknown)} (allowed: provider, model, temperature)");
        }

        string? provider = Text(map, "provider");
        if (provider is not null)
        {
            provider = provider.Trim().ToLowerInvariant();
            if (!SupportedProviders.Contains(provider))
            {
                throw new InvalidOperationException(
                    $"{source}: unknown model.provider '{provider}'; must be one of {string.Join(", ", SupportedProviders)}");
            }
        }

        var model = Text(map, "model")?.Trim();
        if (map.ContainsKey("model") && string.IsNullOrEmpty(model))
        {
            throw new InvalidOperationException($"{source}: model.model must be a non-empty string");
        }

        double? temperature = null;
        var rawTemperature = Text(map, "temperature");
        if (rawTemperature is not null)
        {
            if (!double.TryParse(rawTemperature, NumberStyles.Float, CultureInfo.InvariantCulture, out var t)
                || t < 0 || t > 2)
            {
                throw new InvalidOperationException($"{source}: model.temperature must be a number between 0 and 2");
            }

            temperature = t;
        }

        return provider is null && model is null && temperature is null
            ? null
            : new ModelOverride(provider, model, temperature);
    }

    private static string? Text(Dictionary<object, object> map, string key) =>
        map.TryGetValue(key, out var v) ? v?.ToString() : null;

    /// <summary>
    /// The settings an agent with this block runs under: provider and model replaced, nothing else.
    /// <c>replay</c> ignores the block entirely (fixtures are keyed by request, not model).
    /// </summary>
    public AgentSettings ApplyTo(AgentSettings settings)
    {
        var global = settings.LlmProvider?.Trim().ToLowerInvariant() ?? "openai";
        if (global == "replay")
        {
            return settings;
        }

        var provider = Provider ?? global;
        var effective = settings with { LlmProvider = provider };
        switch (provider)
        {
            case "azure":
                return Model is null ? effective : effective with { AzureOpenAiDeployment = Model };
            case "anthropic":
                return effective with { LlmModel = Model ?? settings.AnthropicModel };
            default:
                if (global == "anthropic" && Model is null)
                {
                    // LlmModel currently holds a Claude id; there is no sensible OpenAI model to fall back to.
                    throw new InvalidOperationException(
                        "model.model is required when an agent switches from LLM_PROVIDER=anthropic to openai");
                }

                // LLM_BASE_URL belongs to the global openai endpoint; an agent switching *to* openai
                // from another global provider talks to api.openai.com.
                effective = global == "openai" ? effective : effective with { LlmBaseUrl = "" };
                return Model is null ? effective : effective with { LlmModel = Model };
        }
    }
}
