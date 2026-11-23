using ECommerceAgents.Shared.Agents;
using ECommerceAgents.Shared.Configuration;
using Microsoft.Extensions.Logging;

namespace ECommerceAgents.Shared.Logging;

/// <summary>
/// The one structured startup line each service logs: provider, model, embedding model, auth mode, pricing source and
/// budget mode. Only non-secret facts: no key, no password, only the host part of a base URL. Python twin:
/// <c>shared/startup_info.py</c>; the field names and their order are the same on both stacks.
/// </summary>
public static class StartupLog
{
    /// <summary>The fields, in the order they are written.</summary>
    public static readonly string[] Fields =
    [
        "service", "stack", "profile", "provider", "model", "llm_api", "llm_host",
        "embedding_provider", "embedding_model", "auth_mode", "pricing", "budget_mode",
    ];

    /// <summary>The summary for <paramref name="settings"/>. <paramref name="environment"/> defaults to the process environment.</summary>
    public static IReadOnlyDictionary<string, string> Summary(
        string service, AgentSettings settings, Func<string, string?>? environment = null)
    {
        ArgumentNullException.ThrowIfNull(settings);
        var env = environment ?? Environment.GetEnvironmentVariable;
        var provider = (settings.LlmProvider ?? "").Trim().ToLowerInvariant();

        string embeddingProvider;
        try
        {
            embeddingProvider = EmbeddingClientFactory.ResolveProvider(settings);
        }
        catch (InvalidOperationException)
        {
            embeddingProvider = "invalid";
        }

        var embeddingModel = embeddingProvider == "azure" && !string.IsNullOrWhiteSpace(settings.AzureEmbeddingDeployment)
            ? settings.AzureEmbeddingDeployment
            : settings.EmbeddingModel;

        return new Dictionary<string, string>
        {
            ["service"] = service,
            ["stack"] = "dotnet",
            ["profile"] = NonEmpty(env("RUN_PROFILE"), "none"),
            ["provider"] = provider,
            ["model"] = provider switch
            {
                "azure" => settings.AzureOpenAiDeployment,
                "anthropic" => settings.AnthropicModel,
                "replay" => "replay",
                _ => settings.LlmModel,
            },
            // The .NET stack always speaks Chat Completions; LLM_API is Python-only.
            ["llm_api"] = provider == "openai" ? "chat_completions" : "n/a",
            ["llm_host"] = provider == "openai" ? Host(settings.LlmBaseUrl) : "",
            ["embedding_provider"] = embeddingProvider,
            ["embedding_model"] = embeddingModel,
            ["auth_mode"] = settings.AuthMode,
            ["pricing"] = PricingSource(env("PRICING_FILE"), env("PRICING_OVERRIDES_FILE")),
            ["budget_mode"] = settings.CostBudgetMode,
        };
    }

    /// <summary>"shipped", "file:name" or either plus "+overrides:name". File names only, never a directory.</summary>
    public static string PricingSource(string? file, string? overrides)
    {
        var baseName = string.IsNullOrWhiteSpace(file) ? "shipped" : $"file:{Path.GetFileName(file)}";
        return string.IsNullOrWhiteSpace(overrides) ? baseName : $"{baseName}+overrides:{Path.GetFileName(overrides)}";
    }

    /// <summary>Host and port of a URL; never credentials, path or query.</summary>
    public static string Host(string? url)
    {
        if (string.IsNullOrWhiteSpace(url) || !Uri.TryCreate(url, UriKind.Absolute, out var uri))
        {
            return "";
        }

        return uri.IsDefaultPort ? uri.Host : $"{uri.Host}:{uri.Port}";
    }

    public static string Format(IReadOnlyDictionary<string, string> summary) =>
        "startup " + string.Join(" ", Fields.Select(f =>
            $"{f}={(summary.TryGetValue(f, out var v) && !string.IsNullOrEmpty(v) ? LogSafe.Clean(v) : "-")}"));

    /// <summary>Logs the line. Never throws: a startup log must not stop a service.</summary>
    public static void Log(ILogger logger, string service, AgentSettings settings)
    {
        try
        {
            logger.LogInformation("{StartupLine}", Format(Summary(service, settings)));
        }
        catch (Exception ex) when (ex is not OutOfMemoryException)
        {
            logger.LogWarning("startup summary unavailable");
        }
    }

    private static string NonEmpty(string? value, string fallback) =>
        string.IsNullOrWhiteSpace(value) ? fallback : value;
}
