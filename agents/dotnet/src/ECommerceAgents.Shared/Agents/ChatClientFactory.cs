using System.ClientModel;
using Azure.AI.OpenAI;
using ECommerceAgents.Shared.Configuration;
using OpenAI;
using Microsoft.Extensions.AI;
using OpenAI.Chat;

namespace ECommerceAgents.Shared.Agents;

/// <summary>
/// Mirrors Python's <c>shared/factory.py</c>: builds the chat client every agent in the
/// codebase consumes. Switch providers via <see cref="AgentSettings.LlmProvider"/>; Azure
/// picks up both the native key name (<c>AZURE_OPENAI_KEY</c>) and the MAF-doc alias
/// (<c>AZURE_OPENAI_API_KEY</c>) thanks to <see cref="AgentSettingsLoader"/>.
/// </summary>
/// <remarks>
/// <see cref="Create"/> returns <see cref="IChatClient"/> rather than the OpenAI SDK's
/// concrete <c>ChatClient</c>. That abstraction is the seam a non-HTTP provider needs:
/// Python swaps in a fixture-replaying client via <c>LLM_PROVIDER=replay</c> to make its
/// eval suite deterministic and free, and with a concrete return type there was nowhere
/// on the .NET side to slot an equivalent. <c>LLM_PROVIDER</c> was also accepted
/// unvalidated, so an unknown value silently fell through to the OpenAI branch and
/// demanded a key — <c>replay</c> included.
/// </remarks>
public static class ChatClientFactory
{
    /// <summary>Provider values this factory implements.</summary>
    private static readonly string[] KnownProviders = ["openai", "azure", "replay"];

    /// <summary>
    /// Builds the chat client for the configured provider.
    /// </summary>
    public static IChatClient Create(AgentSettings settings)
    {
        ArgumentNullException.ThrowIfNull(settings);

        var provider = settings.LlmProvider?.Trim().ToLowerInvariant() ?? "openai";
        if (!KnownProviders.Contains(provider))
        {
            // Named explicitly rather than falling through: a typo used to become an
            // OpenAI attempt with a confusing "OPENAI_API_KEY is required" instead of
            // "that provider does not exist".
            throw new InvalidOperationException(
                $"LLM_PROVIDER='{settings.LlmProvider}' is not supported. Use {string.Join(" or ", KnownProviders)}.");
        }

        if (provider == "replay")
        {
            // No network, no credentials, no nondeterminism. Answers come from recorded
            // fixtures keyed on the request, which is what lets an eval suite attribute a
            // score change to a code change.
            return new ReplayChatClient(settings.ReplayFixturesDir);
        }

        return CreateChatClient(settings).AsIChatClient();
    }

    /// <summary>
    /// The concrete OpenAI SDK client, kept for callers that need SDK-specific surface.
    /// Prefer <see cref="Create"/>.
    /// </summary>
    public static ChatClient CreateChatClient(AgentSettings settings)
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

            if (string.IsNullOrWhiteSpace(settings.AzureOpenAiDeployment))
            {
                throw new InvalidOperationException(
                    "AZURE_OPENAI_DEPLOYMENT (or AZURE_OPENAI_DEPLOYMENT_NAME) is required when LLM_PROVIDER=azure"
                );
            }

            var azureClient = new AzureOpenAIClient(
                new Uri(settings.AzureOpenAiEndpoint),
                new ApiKeyCredential(settings.AzureOpenAiKey)
            );
            return azureClient.GetChatClient(settings.AzureOpenAiDeployment);
        }

        if (string.IsNullOrWhiteSpace(settings.OpenAiApiKey))
        {
            throw new InvalidOperationException("OPENAI_API_KEY is required when LLM_PROVIDER=openai");
        }

        // LLM_BASE_URL points this at any OpenAI-compatible server — Ollama,
        // LM Studio, llama.cpp, vLLM, OpenRouter, GitHub Models — matching
        // Python's shared/factory.py. Local servers usually ignore the API key
        // but the SDK still requires a non-empty one, hence the check above
        // applying either way.
        var openAiOptions = new OpenAIClientOptions();
        if (!string.IsNullOrWhiteSpace(settings.LlmBaseUrl))
        {
            openAiOptions.Endpoint = new Uri(settings.LlmBaseUrl);
        }

        var openAi = new OpenAIClient(new ApiKeyCredential(settings.OpenAiApiKey), openAiOptions);
        return openAi.GetChatClient(settings.LlmModel);
    }
}
