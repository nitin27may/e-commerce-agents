using System.Net;
using System.Text;
using System.Text.Json;
using ECommerceAgents.Shared.Agents;
using ECommerceAgents.Shared.Configuration;
using FluentAssertions;
using Microsoft.Extensions.AI;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// A chat request through the Azure OpenAI client built by <see cref="ChatClientFactory"/> must serialise and
/// reach the wire. The previous <c>Azure.AI.OpenAI</c> pin was binary-incompatible with the <c>OpenAI</c>
/// package that <c>Microsoft.Agents.AI.OpenAI</c> brings in, and every request threw
/// <see cref="MissingMethodException"/> before it was sent; the construction-only tests could not see that.
/// A stub handler stands in for the network; a live call to Azure is still not covered.
/// </summary>
[Collection(nameof(ProcessEnvironmentCollection))]
public sealed class AzureOpenAiChatClientTests
{
    private sealed class StubHandler : HttpMessageHandler
    {
        public List<(HttpRequestMessage Request, string Body)> Seen { get; } = [];

        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct)
        {
            Seen.Add((request, request.Content is null ? "" : await request.Content.ReadAsStringAsync(ct)));
            return new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent(CannedResponse, Encoding.UTF8, "application/json"),
            };
        }
    }

    private const string CannedResponse = """
        {"id":"chatcmpl-1","object":"chat.completion","created":1700000000,"model":"gpt-4.1",
         "choices":[{"index":0,"finish_reason":"stop",
           "message":{"role":"assistant","content":"There are 42 units in stock."}}],
         "usage":{"prompt_tokens":11,"completion_tokens":8,"total_tokens":19}}
        """;

    [Fact]
    public async Task AzureNativeChatClient_CompleteChat_ReachesTheWire()
    {
        var settings = new AgentSettings
        {
            LlmProvider = "azure",
            AzureOpenAiEndpoint = "https://example.openai.azure.com/",
            AzureOpenAiKey = "test-key",
            AzureOpenAiDeployment = "gpt-4.1-deploy",
        };
        var handler = new StubHandler();
        using var http = new HttpClient(handler);

        var native = ChatClientFactory.CreateChatClient(settings, http);
        var result = await native.CompleteChatAsync(
            [OpenAI.Chat.ChatMessage.CreateUserMessage("hi")],
            new OpenAI.Chat.ChatCompletionOptions { Temperature = 0.2f, MaxOutputTokenCount = 64 });

        handler.Seen.Should().ContainSingle();
        result.Value.Content[0].Text.Should().Be("There are 42 units in stock.");
    }

    [Fact]
    public async Task AzureChatStreaming_ReachesTheWire()
    {
        var settings = new AgentSettings
        {
            LlmProvider = "azure",
            AzureOpenAiEndpoint = "https://example.openai.azure.com/",
            AzureOpenAiKey = "test-key",
            AzureOpenAiDeployment = "gpt-4.1-deploy",
        };
        var handler = new StubHandler();
        using var http = new HttpClient(handler);

        IChatClient client = ChatClientFactory.CreateChatClient(settings, http).AsIChatClient();
        try
        {
            await foreach (var _ in client.GetStreamingResponseAsync([new ChatMessage(ChatRole.User, "hi")]))
            {
            }
        }
        catch (MissingMethodException)
        {
            throw;
        }
        catch (Exception)
        {
            // The canned body is not an SSE stream; only serialisation reaching the wire matters here.
        }

        handler.Seen.Should().ContainSingle();
    }

    [Fact]
    public async Task AzureChatRequest_ReachesTheWireAndParsesTheResponse()
    {
        var settings = new AgentSettings
        {
            LlmProvider = "azure",
            AzureOpenAiEndpoint = "https://example.openai.azure.com/",
            AzureOpenAiKey = "test-key",
            AzureOpenAiDeployment = "gpt-4.1-deploy",
        };
        var handler = new StubHandler();
        using var http = new HttpClient(handler);

        IChatClient client = ChatClientFactory.CreateChatClient(settings, http).AsIChatClient();
        var response = await client.GetResponseAsync(
            [new ChatMessage(ChatRole.User, "How many headphones are in stock?")],
            new ChatOptions
            {
                Temperature = 0.2f,
                MaxOutputTokens = 64,
                Tools = [AIFunctionFactory.Create((string sku) => 42, "get_stock", "Units in stock")],
            });

        handler.Seen.Should().ContainSingle();
        var (request, body) = handler.Seen[0];
        request.Method.Should().Be(HttpMethod.Post);
        request.RequestUri!.AbsolutePath.Should().Be("/openai/deployments/gpt-4.1-deploy/chat/completions");
        request.RequestUri.Query.Should().Contain("api-version=");
        request.Headers.GetValues("api-key").Should().ContainSingle().Which.Should().Be("test-key");

        using var json = JsonDocument.Parse(body);
        json.RootElement.GetProperty("messages")[0].GetProperty("content").ToString()
            .Should().Contain("How many headphones");

        response.Text.Should().Be("There are 42 units in stock.");
        response.Usage!.InputTokenCount.Should().Be(11);
        response.Usage.OutputTokenCount.Should().Be(8);
    }
}
