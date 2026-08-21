using System.ClientModel;
using Azure.AI.OpenAI;
using ECommerceAgents.Shared.Configuration;
using OpenAI;
using OpenAI.Chat;

namespace ECommerceAgents.Shared.Agents;

/// <summary>
/// Mirrors Python's <c>shared/factory.py</c>: builds the OpenAI /
/// Azure OpenAI chat client that every agent in the codebase will
/// consume. Switch between providers via
/// <see cref="AgentSettings.LlmProvider"/>; Azure picks up both the
/// native key name (<c>AZURE_OPENAI_KEY</c>) and the MAF-doc alias
/// (<c>AZURE_OPENAI_API_KEY</c>) thanks to <see cref="AgentSettingsLoader"/>.
/// </summary>
public static class ChatClientFactory
{
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
        // LM Studio, llama.cpp, vLLM, OpenRouter — matching
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
