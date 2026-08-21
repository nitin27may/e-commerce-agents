using System.ClientModel;
using Azure.AI.OpenAI;
using ECommerceAgents.Shared.Configuration;
using OpenAI;
using OpenAI.Embeddings;

namespace ECommerceAgents.Shared.Agents;

/// <summary>
/// Mirrors Python's <c>shared/factory.py::get_embeddings_client</c> +
/// <c>get_embedding_model</c>: builds the OpenAI / Azure OpenAI embeddings
/// client used for pgvector-backed semantic search. Same provider switch
/// as <see cref="ChatClientFactory"/>, kept as a separate factory since
/// embeddings and chat completions are different client/model pairs.
/// </summary>
public static class EmbeddingClientFactory
{
    /// <summary>
    /// The embedding provider for the configured LLM provider.
    /// </summary>
    /// <remarks>
    /// Prefer this over <see cref="CreateEmbeddingClient"/>: it is the only
    /// entry point that honours <c>LLM_PROVIDER=replay</c>. Resolving the
    /// concrete client directly is what made product-discovery fail to start
    /// under replay, since a sealed SDK type has no offline implementation.
    /// </remarks>
    public static IEmbeddingProvider CreateProvider(AgentSettings settings)
    {
        ArgumentNullException.ThrowIfNull(settings);
        return string.Equals(settings.LlmProvider, "replay", StringComparison.OrdinalIgnoreCase)
            ? new ReplayEmbeddingProvider()
            : new OpenAIEmbeddingProvider(CreateEmbeddingClient(settings));
    }

    public static EmbeddingClient CreateEmbeddingClient(AgentSettings settings)
    {
        if (string.Equals(settings.LlmProvider, "azure", StringComparison.OrdinalIgnoreCase))
        {
            if (string.IsNullOrWhiteSpace(settings.AzureOpenAiEndpoint))
            {
                throw new InvalidOperationException("AZURE_OPENAI_ENDPOINT is required when LLM_PROVIDER=azure");
            }

            if (string.IsNullOrWhiteSpace(settings.AzureOpenAiKey))
            {
                throw new InvalidOperationException(
                    "AZURE_OPENAI_KEY (or AZURE_OPENAI_API_KEY) is required when LLM_PROVIDER=azure"
                );
            }

            var azureClient = new AzureOpenAIClient(
                new Uri(settings.AzureOpenAiEndpoint),
                new ApiKeyCredential(settings.AzureOpenAiKey)
            );
            return azureClient.GetEmbeddingClient(EmbeddingModelName(settings));
        }

        if (string.IsNullOrWhiteSpace(settings.OpenAiApiKey))
        {
            throw new InvalidOperationException("OPENAI_API_KEY is required when LLM_PROVIDER=openai");
        }

        // Same LLM_BASE_URL override as ChatClientFactory, so a self-hosted
        // OpenAI-compatible server (Ollama's nomic-embed-text, LM Studio, vLLM)
        // can serve embeddings too rather than forcing api.openai.com.
        var openAiOptions = new OpenAIClientOptions();
        if (!string.IsNullOrWhiteSpace(settings.LlmBaseUrl))
        {
            openAiOptions.Endpoint = new Uri(settings.LlmBaseUrl);
        }

        var openAi = new OpenAIClient(new ApiKeyCredential(settings.OpenAiApiKey), openAiOptions);
        return openAi.GetEmbeddingClient(settings.EmbeddingModel);
    }

    /// <summary>Azure deployment name when set, else the plain model name — matches
    /// Python's <c>get_embedding_model</c> precedence exactly.</summary>
    private static string EmbeddingModelName(AgentSettings settings) =>
        !string.IsNullOrWhiteSpace(settings.AzureEmbeddingDeployment)
            ? settings.AzureEmbeddingDeployment
            : settings.EmbeddingModel;
}
