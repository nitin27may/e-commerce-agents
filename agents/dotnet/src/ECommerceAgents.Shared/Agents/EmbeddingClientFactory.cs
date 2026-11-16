using System.ClientModel;
using Azure.AI.OpenAI;
using ECommerceAgents.Shared.Configuration;
using OpenAI;
using OpenAI.Embeddings;

namespace ECommerceAgents.Shared.Agents;

/// <summary>
/// Mirrors Python's <c>shared/factory.py::get_embeddings_client</c> +
/// <c>get_embedding_model</c> and <c>shared/embedding_config.py</c>: builds the OpenAI / Azure OpenAI /
/// OpenAI-compatible embeddings client used for pgvector-backed semantic search. Embeddings are configured
/// apart from chat (<c>EMBEDDING_PROVIDER</c>, <c>EMBEDDING_BASE_URL</c>, <c>EMBEDDING_API_KEY</c>,
/// <c>EMBEDDING_MODEL</c>, <c>EMBEDDING_DIM</c>); with <c>EMBEDDING_PROVIDER</c> empty they follow
/// <c>LLM_PROVIDER</c> exactly as before.
/// </summary>
public static class EmbeddingClientFactory
{
    /// <summary>Providers <c>EMBEDDING_PROVIDER</c> may name.</summary>
    public static readonly string[] KnownProviders = ["openai", "azure", "openai_compatible", "replay"];

    /// <summary>
    /// The provider in effect: <c>EMBEDDING_PROVIDER</c>, else the chat provider's. Throws on an unknown value.
    /// </summary>
    public static string ResolveProvider(AgentSettings settings)
    {
        var chosen = settings.EmbeddingProvider?.Trim().ToLowerInvariant() ?? "";
        if (chosen.Length > 0)
        {
            return KnownProviders.Contains(chosen)
                ? chosen
                : throw new InvalidOperationException(
                    $"Unknown EMBEDDING_PROVIDER '{settings.EmbeddingProvider}'. Must be one of "
                    + $"{string.Join(", ", KnownProviders)} (or empty to follow LLM_PROVIDER).");
        }

        return settings.LlmProvider?.Trim().ToLowerInvariant() ?? "openai";
    }

    private static bool FollowsChatProvider(AgentSettings settings) =>
        string.IsNullOrWhiteSpace(settings.EmbeddingProvider);

    /// <summary>
    /// The embedding provider for the configured settings.
    /// </summary>
    /// <remarks>
    /// Prefer this over <see cref="CreateEmbeddingClient"/>: it is the only
    /// entry point that honours <c>replay</c>. Resolving the
    /// concrete client directly is what made product-discovery fail to start
    /// under replay, since a sealed SDK type has no offline implementation.
    /// </remarks>
    public static IEmbeddingProvider CreateProvider(AgentSettings settings)
    {
        ArgumentNullException.ThrowIfNull(settings);
        var provider = ResolveProvider(settings);
        if (provider == "replay")
        {
            return new ReplayEmbeddingProvider(settings.EmbeddingDim);
        }

        if (provider == "anthropic")
        {
            // Anthropic has no embeddings API, so the chat provider cannot supply them. Say what is
            // missing instead of falling through to an OpenAI path that would demand a key the user
            // never meant to set.
            const string reason =
                "LLM_PROVIDER=anthropic has no embeddings API; set EMBEDDING_PROVIDER (openai, azure, "
                + "openai_compatible) and its key or EMBEDDING_BASE_URL (see docs/configuration.md)";
            Serilog.Log.Warning("Embeddings unavailable ({Reason}); semantic search will use full-text only", reason);
            return new UnavailableEmbeddingProvider(reason);
        }

        try
        {
            return new OpenAIEmbeddingProvider(
                CreateEmbeddingClient(settings),
                SupportsDimensions(EmbeddingModelName(settings)) ? settings.EmbeddingDim : null,
                settings.EmbeddingDim);
        }
        catch (InvalidOperationException ex)
        {
            // Missing key or endpoint. Degrade rather than refuse to start:
            // semantic search falls back to full-text (ProductTools), which is
            // what Python's semantic_search does when its embedding call raises.
            Serilog.Log.Warning("Embeddings unavailable ({Reason}); semantic search will use full-text only", ex.Message);
            return new UnavailableEmbeddingProvider(ex.Message);
        }
    }

    public static EmbeddingClient CreateEmbeddingClient(AgentSettings settings)
    {
        var provider = ResolveProvider(settings);
        var model = EmbeddingModelName(settings);

        if (provider == "azure")
        {
            var endpoint = FirstNonBlank(settings.EmbeddingBaseUrl, settings.AzureOpenAiEndpoint);
            var key = FirstNonBlank(settings.EmbeddingApiKey, settings.AzureOpenAiKey);
            if (endpoint is null || key is null)
            {
                throw new InvalidOperationException(
                    "Azure embeddings need AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_KEY (or EMBEDDING_BASE_URL and EMBEDDING_API_KEY)");
            }

            return new AzureOpenAIClient(new Uri(endpoint), new ApiKeyCredential(key)).GetEmbeddingClient(model);
        }

        if (provider == "openai_compatible")
        {
            if (string.IsNullOrWhiteSpace(settings.EmbeddingBaseUrl))
            {
                throw new InvalidOperationException(
                    "EMBEDDING_PROVIDER=openai_compatible requires EMBEDDING_BASE_URL, e.g. http://localhost:11434/v1 for Ollama");
            }

            // Local servers ignore the key, but the SDK insists on a non-empty one.
            var localKey = FirstNonBlank(settings.EmbeddingApiKey, settings.OpenAiApiKey) ?? "not-needed";
            return new OpenAIClient(
                new ApiKeyCredential(localKey),
                new OpenAIClientOptions { Endpoint = new Uri(settings.EmbeddingBaseUrl) }).GetEmbeddingClient(model);
        }

        var openAiKey = FirstNonBlank(settings.EmbeddingApiKey, settings.OpenAiApiKey);
        if (openAiKey is null)
        {
            throw new InvalidOperationException("OPENAI_API_KEY (or EMBEDDING_API_KEY) is required for OpenAI embeddings");
        }

        // Following the chat provider keeps today's LLM_BASE_URL (a self-hosted OpenAI-compatible server such as
        // Ollama's nomic-embed-text); an explicit EMBEDDING_PROVIDER=openai means api.openai.com unless
        // EMBEDDING_BASE_URL says otherwise.
        var baseUrl = FirstNonBlank(settings.EmbeddingBaseUrl, FollowsChatProvider(settings) ? settings.LlmBaseUrl : null);
        var openAiOptions = new OpenAIClientOptions();
        if (baseUrl is not null)
        {
            openAiOptions.Endpoint = new Uri(baseUrl);
        }

        return new OpenAIClient(new ApiKeyCredential(openAiKey), openAiOptions).GetEmbeddingClient(model);
    }

    /// <summary>OpenAI's text-embedding-3 family is the only one that accepts a <c>dimensions</c> parameter.</summary>
    public static bool SupportsDimensions(string model) =>
        model.Trim().StartsWith("text-embedding-3", StringComparison.OrdinalIgnoreCase);

    /// <summary>Azure deployment name when the provider is azure and one is set, else the plain model name —
    /// matches Python's <c>embedding_model_name</c> precedence exactly.</summary>
    public static string EmbeddingModelName(AgentSettings settings) =>
        ResolveProvider(settings) == "azure" && !string.IsNullOrWhiteSpace(settings.AzureEmbeddingDeployment)
            ? settings.AzureEmbeddingDeployment
            : settings.EmbeddingModel;

    private static string? FirstNonBlank(params string?[] values) =>
        values.FirstOrDefault(v => !string.IsNullOrWhiteSpace(v));
}
