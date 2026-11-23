using System.ClientModel;
using System.ClientModel.Primitives;
using Anthropic;
using Azure.AI.OpenAI;
using Azure.Core;
using ECommerceAgents.Shared.Configuration;
using OpenAI;
using Microsoft.Extensions.AI;
using OpenAI.Chat;
using ECommerceAgents.Shared.Prompts;

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
    private static readonly string[] KnownProviders = ["openai", "azure", "anthropic", "replay"];

    /// <summary>
    /// Builds the chat client for the configured provider.
    /// </summary>
    /// <remarks>
    /// <paramref name="agentName"/> (a <c>config/prompts/&lt;agentName&gt;.yaml</c> stem) plus
    /// <paramref name="prompts"/> selects that agent's optional <c>model:</c> block; anything it leaves
    /// out, and every agent without one, uses <paramref name="settings"/>. Under <c>replay</c> the block
    /// is ignored. Python twin: <c>get_chat_client(agent_name)</c>.
    /// </remarks>
    public static IChatClient Create(AgentSettings settings, string? agentName = null, PromptLoader? prompts = null)
    {
        ArgumentNullException.ThrowIfNull(settings);
        GatewayHeaders.NoteAgent(agentName);

        var agentOverride = agentName is null ? null : prompts?.LoadModelOverride(agentName);
        if (agentOverride is not null)
        {
            var client = Create(agentOverride.ApplyTo(settings), agentName);
            return agentOverride.Temperature is { } t
                // Applied as the request leaves, so it also beats the agent's own ChatOptions.Temperature
                // (SpecialistAgentFactory sets that from LLM_TEMPERATURE).
                ? client.AsBuilder().ConfigureOptions(o => o.Temperature = (float)t).Build()
                : client;
        }

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
            //
            // A recorder is attached only when RECORD=true, so the default replay path
            // still cannot reach the network by accident — a fixture miss stays fatal
            // rather than quietly becoming a paid API call.
            IChatClient? recorder = null;
            if (settings.Record)
            {
                recorder = CreateProviderClient(settings with { LlmProvider = settings.ReplayRecordProvider }, agentName);
            }

            return new ReplayChatClient(settings.ReplayFixturesDir, recorder);
        }

        return CreateProviderClient(settings, agentName);
    }

    private static IChatClient CreateProviderClient(AgentSettings settings, string? agentName) =>
        string.Equals(settings.LlmProvider, "anthropic", StringComparison.OrdinalIgnoreCase)
            ? CreateAnthropicClient(settings, agentName)
            : CreateChatClient(settings, null, null, null, agentName).AsIChatClient();

    /// <summary>
    /// Anthropic's <c>messages</c> API requires <c>max_tokens</c> on every request; the OpenAI-shaped
    /// providers do not. This is the default the adapter applies when a caller's options set none.
    /// </summary>
    public const int AnthropicDefaultMaxOutputTokens = 4096;

    /// <summary>
    /// Claude through the official <c>Anthropic</c> package's <see cref="IChatClient"/> adapter (.NET only —
    /// the Python connector needs MAF core 1.24+). The model is <see cref="AgentSettings.LlmModel"/>, which the
    /// loader fills from <c>ANTHROPIC_MODEL</c> (default <c>claude-sonnet-5-5</c>). <paramref name="httpClient"/>
    /// exists so a test can put a stub handler under the real SDK.
    /// </summary>
    public static IChatClient CreateAnthropicClient(AgentSettings settings, HttpClient? httpClient = null) =>
        BuildAnthropicClient(settings, httpClient);

    /// <summary>
    /// The Anthropic client with the gateway attribution headers (<see cref="GatewayHeaders"/>). Without a caller's
    /// <paramref name="httpClient"/> it owns an <see cref="HttpClient"/> whose handler adds them; a caller's client is used
    /// as is (a test seam that predates the headers: use the <see cref="HttpMessageHandler"/> overload to see them).
    /// </summary>
    public static IChatClient CreateAnthropicClient(AgentSettings settings, string? agentName, HttpClient? httpClient = null) =>
        BuildAnthropicClient(
            settings,
            httpClient ?? (settings.GatewayHeadersEnabled
                ? new HttpClient(GatewayHeaders.Wrap(new HttpClientHandler(), settings, agentName))
                : null));

    /// <summary>A stub <paramref name="handler"/> under the real SDK, wrapped by the gateway-header handler (tests).</summary>
    public static IChatClient CreateAnthropicClient(AgentSettings settings, string? agentName, HttpMessageHandler handler) =>
        BuildAnthropicClient(settings, new HttpClient(GatewayHeaders.Wrap(handler, settings, agentName)));

    private static IChatClient BuildAnthropicClient(AgentSettings settings, HttpClient? httpClient)
    {
        if (string.IsNullOrWhiteSpace(settings.AnthropicApiKey))
        {
            // Names the variable only — never the value.
            throw new InvalidOperationException("ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic");
        }

        var client = httpClient is null
            ? new AnthropicClient { ApiKey = settings.AnthropicApiKey }
            : new AnthropicClient { ApiKey = settings.AnthropicApiKey, HttpClient = httpClient };
        return client.AsIChatClient(settings.LlmModel, AnthropicDefaultMaxOutputTokens);
    }

    /// <summary>
    /// The concrete OpenAI SDK client, kept for callers that need SDK-specific surface.
    /// Prefer <see cref="Create"/>. <paramref name="httpClient"/> exists so a test can put a stub handler
    /// under the real SDK.
    /// </summary>
    public static ChatClient CreateChatClient(AgentSettings settings) =>
        CreateChatClient(settings, null, null, null, null);

    /// <summary>The same client over a caller-supplied <see cref="HttpClient"/> (a stub handler in tests).</summary>
    public static ChatClient CreateChatClient(AgentSettings settings, HttpClient? httpClient, string? agentName = null) =>
        CreateChatClient(settings, null, null, httpClient, agentName);

    /// <summary>
    /// <paramref name="credential"/> and <paramref name="options"/> exist so a test can put a fake
    /// <see cref="TokenCredential"/> and a stub transport under the real SDK; production passes neither.
    /// </summary>
    public static ChatClient CreateChatClient(
        AgentSettings settings, TokenCredential? credential, AzureOpenAIClientOptions? options) =>
        CreateChatClient(settings, credential, options, null, null);

    private static ChatClient CreateChatClient(
        AgentSettings settings,
        TokenCredential? credential,
        AzureOpenAIClientOptions? options,
        HttpClient? httpClient,
        string? agentName)
    {
        if (string.Equals(settings.LlmProvider, "azure", StringComparison.OrdinalIgnoreCase))
        {
            if (string.IsNullOrWhiteSpace(settings.AzureOpenAiEndpoint))
            {
                throw new InvalidOperationException("AZURE_OPENAI_ENDPOINT is required when LLM_PROVIDER=azure");
            }

            if (!AzureIdentity.IsIdentity(settings) && string.IsNullOrWhiteSpace(settings.AzureOpenAiKey))
            {
                throw new InvalidOperationException(
                    "AZURE_OPENAI_KEY (or AZURE_OPENAI_API_KEY) is required when LLM_PROVIDER=azure "
                    + "(or set AZURE_OPENAI_AUTH=identity)"
                );
            }

            if (string.IsNullOrWhiteSpace(settings.AzureOpenAiDeployment))
            {
                throw new InvalidOperationException(
                    "AZURE_OPENAI_DEPLOYMENT (or AZURE_OPENAI_DEPLOYMENT_NAME) is required when LLM_PROVIDER=azure"
                );
            }

            var azureOptions = options ?? new AzureOpenAIClientOptions();
            if (httpClient is not null)
            {
                azureOptions.Transport = new HttpClientPipelineTransport(httpClient);
            }

            GatewayHeaders.Apply(azureOptions, settings, agentName);

            var endpoint = new Uri(settings.AzureOpenAiEndpoint);
            var azureClient = AzureIdentity.IsIdentity(settings)
                // No key is read or sent: the SDK asks the credential for a bearer token.
                ? new AzureOpenAIClient(endpoint, credential ?? AzureIdentity.CreateCredential(settings), azureOptions)
                : new AzureOpenAIClient(endpoint, new ApiKeyCredential(settings.AzureOpenAiKey), azureOptions);
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
        if (httpClient is not null)
        {
            openAiOptions.Transport = new HttpClientPipelineTransport(httpClient);
        }

        GatewayHeaders.Apply(openAiOptions, settings, agentName);

        if (!string.IsNullOrWhiteSpace(settings.LlmBaseUrl))
        {
            openAiOptions.Endpoint = new Uri(settings.LlmBaseUrl);
        }

        var openAi = new OpenAIClient(new ApiKeyCredential(settings.OpenAiApiKey), openAiOptions);
        return openAi.GetChatClient(settings.LlmModel);
    }
}
