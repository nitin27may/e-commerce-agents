using System.Net;
using System.Text;
using ECommerceAgents.Shared.Agents;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using FluentAssertions;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// Gateway attribution headers (AZ-4): <c>x-agent-name</c>, <c>x-run-id</c>, <c>x-eval-run-id</c> on every model call,
/// chat and embeddings, for the OpenAI, Azure OpenAI, OpenAI-compatible and Anthropic clients. A stub handler records
/// the request headers. Python twin: <c>tests/test_gateway_headers.py</c>.
/// </summary>
[Collection(nameof(ProcessEnvironmentCollection))]
public sealed class GatewayHeadersTests
{
    private const string Chat = """
        {"id":"c1","object":"chat.completion","created":1700000000,"model":"gpt-4.1",
         "choices":[{"index":0,"finish_reason":"stop","message":{"role":"assistant","content":"ok"}}],
         "usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}
        """;

    private const string Embedding = """
        {"object":"list","data":[{"object":"embedding","index":0,"embedding":[0.1,0.2]}],
         "model":"text-embedding-3-small","usage":{"prompt_tokens":1,"total_tokens":1}}
        """;

    private const string Claude = """
        {"id":"msg_1","type":"message","role":"assistant","model":"claude-sonnet-5-5",
         "content":[{"type":"text","text":"ok"}],"stop_reason":"end_turn","stop_sequence":null,
         "usage":{"input_tokens":1,"output_tokens":1}}
        """;

    private sealed class Recorder(string body) : HttpMessageHandler
    {
        public List<HttpRequestMessage> Seen { get; } = [];

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct)
        {
            Seen.Add(request);
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent(body, Encoding.UTF8, "application/json"),
            });
        }
    }

    private static string? Header(HttpRequestMessage r, string name) =>
        r.Headers.TryGetValues(name, out var v) ? v.Single() : null;

    private static readonly string[] Gateway =
        [GatewayHeaders.AgentNameHeader, GatewayHeaders.RunIdHeader, GatewayHeaders.EvalRunIdHeader];

    private static AgentSettings Openai() =>
        new() { LlmProvider = "openai", OpenAiApiKey = "sk-test", EvalRunId = "eval-1" };

    private static AgentSettings Azure() => new()
    {
        LlmProvider = "azure",
        AzureOpenAiEndpoint = "https://example.openai.azure.com/",
        AzureOpenAiKey = "az-key",
        AzureOpenAiDeployment = "gpt-4.1",
        EvalRunId = "eval-1",
    };

    private static async Task ChatAsync(OpenAI.Chat.ChatClient client) =>
        await client.CompleteChatAsync([OpenAI.Chat.ChatMessage.CreateUserMessage("hi")]);

    public static TheoryData<string> OpenAiShapes => new() { "openai", "openai-compatible", "azure" };

    [Theory]
    [MemberData(nameof(OpenAiShapes))]
    public async Task Chat_call_carries_the_three_headers(string shape)
    {
        var settings = shape switch
        {
            "azure" => Azure(),
            "openai-compatible" => Openai() with { LlmBaseUrl = "http://localhost:11434/v1" },
            _ => Openai(),
        };
        var handler = new Recorder(Chat);
        using var http = new HttpClient(handler);
        using var scope = RequestContext.Scope("shopper@example.com", "customer", "session-1");

        await ChatAsync(ChatClientFactory.CreateChatClient(settings, http, "product-discovery"));

        var request = handler.Seen.Should().ContainSingle().Subject;
        Header(request, GatewayHeaders.AgentNameHeader).Should().Be("product-discovery");
        Header(request, GatewayHeaders.RunIdHeader).Should().Be(RequestContext.CurrentRunId).And.NotBeEmpty();
        Header(request, GatewayHeaders.EvalRunIdHeader).Should().Be("eval-1");
    }

    [Fact]
    public async Task Anthropic_call_carries_the_three_headers()
    {
        var settings = new AgentSettings
        {
            LlmProvider = "anthropic",
            AnthropicApiKey = "sk-ant-test",
            LlmModel = "claude-sonnet-5-5",
            EvalRunId = "eval-1",
        };
        var handler = new Recorder(Claude);
        using var scope = RequestContext.Scope("", "customer", "");

        var client = ChatClientFactory.CreateAnthropicClient(settings, "order-management", (HttpMessageHandler)handler);
        await client.GetResponseAsync([new Microsoft.Extensions.AI.ChatMessage(Microsoft.Extensions.AI.ChatRole.User, "hi")]);

        var request = handler.Seen.Should().ContainSingle().Subject;
        Header(request, GatewayHeaders.AgentNameHeader).Should().Be("order-management");
        Header(request, GatewayHeaders.RunIdHeader).Should().Be(RequestContext.CurrentRunId);
        Header(request, GatewayHeaders.EvalRunIdHeader).Should().Be("eval-1");
    }

    [Theory]
    [InlineData("openai")]
    [InlineData("azure")]
    public async Task Embeddings_call_carries_the_headers(string provider)
    {
        var settings = provider == "azure"
            ? Azure() with { EmbeddingProvider = "azure" }
            : Openai() with { EmbeddingProvider = "openai" };
        var handler = new Recorder(Embedding);
        using var http = new HttpClient(handler);
        using var scope = RequestContext.Scope("", "customer", "");
        GatewayHeaders.NoteAgent("product-discovery");

        await EmbeddingClientFactory.CreateEmbeddingClient(settings, http).GenerateEmbeddingAsync("hi");

        var request = handler.Seen.Should().ContainSingle().Subject;
        Header(request, GatewayHeaders.AgentNameHeader).Should().Be("product-discovery");
        Header(request, GatewayHeaders.RunIdHeader).Should().Be(RequestContext.CurrentRunId);
        Header(request, GatewayHeaders.EvalRunIdHeader).Should().Be("eval-1");
    }

    [Fact]
    public async Task Disabled_sends_none()
    {
        var handler = new Recorder(Chat);
        using var http = new HttpClient(handler);
        using var scope = RequestContext.Scope("", "customer", "");

        await ChatAsync(ChatClientFactory.CreateChatClient(Openai() with { GatewayHeadersEnabled = false }, http, "a"));

        var request = handler.Seen.Single();
        Gateway.Should().OnlyContain(h => Header(request, h) == null);
    }

    [Fact]
    public async Task Empty_values_are_omitted_and_agents_and_runs_differ_per_request()
    {
        var handler = new Recorder(Chat);
        using var http = new HttpClient(handler);
        var settings = Openai() with { EvalRunId = "" };
        var first = ChatClientFactory.CreateChatClient(settings, http, "order-management");
        var second = ChatClientFactory.CreateChatClient(settings, http, "pricing-promotions");

        await ChatAsync(first); // no scope: no run id
        using (RequestContext.Scope("", "customer", ""))
        {
            await ChatAsync(second);
        }

        using (RequestContext.Scope("", "customer", ""))
        {
            await ChatAsync(first);
        }

        var seen = handler.Seen;
        Header(seen[0], GatewayHeaders.RunIdHeader).Should().BeNull();
        Header(seen[0], GatewayHeaders.EvalRunIdHeader).Should().BeNull();
        seen.Select(r => Header(r, GatewayHeaders.AgentNameHeader))
            .Should().Equal("order-management", "pricing-promotions", "order-management");
        Header(seen[1], GatewayHeaders.RunIdHeader).Should().NotBe(Header(seen[2], GatewayHeaders.RunIdHeader));
    }

    [Fact]
    public void Values_are_sanitised()
    {
        GatewayHeaders.Sanitize("a b\r\nc\0d").Should().Be("abcd");
        GatewayHeaders.Sanitize("café-bot").Should().Be("caf-bot");
        GatewayHeaders.Sanitize("someone@example.com").Should().Be("someoneexample.com");
        GatewayHeaders.Sanitize(new string('x', 1000)).Should().HaveLength(GatewayHeaders.MaxValueLength);
        GatewayHeaders.Sanitize(null).Should().BeEmpty();
    }

    [Fact]
    public async Task No_user_identity_or_secret_in_the_headers()
    {
        var handler = new Recorder(Chat);
        using var http = new HttpClient(handler);
        using var scope = RequestContext.Scope("shopper@example.com", "admin", "11111111-2222-3333-4444-555555555555");

        await ChatAsync(ChatClientFactory.CreateChatClient(Openai(), http, "product-discovery"));

        var request = handler.Seen.Single();
        var values = Gateway.Select(h => Header(request, h) ?? "").ToList();
        foreach (var leaked in new[] { "shopper", "admin", "11111111", "sk-test" })
        {
            values.Should().NotContain(v => v.Contains(leaked));
        }
    }

    [Fact]
    public void Replay_client_is_untouched_by_the_flag()
    {
        // The replay client makes no HTTP call, so headers cannot reach its key (messages and tool schemas only).
        foreach (var enabled in new[] { true, false })
        {
            var client = ChatClientFactory.Create(
                new AgentSettings { LlmProvider = "replay", GatewayHeadersEnabled = enabled }, "product-discovery");
            client.Should().BeOfType<ReplayChatClient>();
        }
    }
}
