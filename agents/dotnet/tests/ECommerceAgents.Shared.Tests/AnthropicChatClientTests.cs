using System.Net;
using System.Text;
using System.Text.Json;
using ECommerceAgents.Shared.Agents;
using ECommerceAgents.Shared.Configuration;
using FluentAssertions;
using Microsoft.Extensions.AI;
using Microsoft.Extensions.Configuration;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// Anthropic (Claude) as a chat provider, .NET only (P-4).
/// </summary>
/// <remarks>
/// <b>What the stub test proves</b>, because it is easy to over-read: that the real <c>Anthropic</c> SDK's
/// <c>IChatClient</c> adapter, wired through <see cref="ChatClientFactory"/>, (a) sends a request in the
/// Messages API shape (path, credential header, model, <c>max_tokens</c>, the tool definition), (b) translates a
/// canned <c>tool_use</c> response into a <see cref="FunctionCallContent"/> that
/// <c>FunctionInvokingChatClient</c> executes, (c) sends the follow-up request carrying a <c>tool_result</c> block
/// for the same <c>tool_use_id</c>, and (d) maps <c>usage</c> into <see cref="UsageDetails"/>.
/// <b>What it does not prove</b>: that Claude chooses the right tool or arguments (tool-choice quality), prompt
/// caching, the shape of real error responses (rate limits, overloads, invalid keys), streaming edge cases, or
/// that the canned JSON matches what api.anthropic.com really returns byte for byte — it matches the published
/// schema, which is the assumption. A live smoke run with a real key is still owed (R2 in the plan).
/// </remarks>
[Collection(nameof(ProcessEnvironmentCollection))]
public sealed class AnthropicChatClientTests
{
    private sealed class StubHandler : HttpMessageHandler
    {
        private readonly Queue<string> _responses;
        public List<(HttpRequestMessage Request, string Body)> Seen { get; } = [];

        public StubHandler(params string[] responses) => _responses = new Queue<string>(responses);

        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct)
        {
            var body = request.Content is null ? "" : await request.Content.ReadAsStringAsync(ct);
            Seen.Add((request, body));
            return new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent(_responses.Dequeue(), Encoding.UTF8, "application/json"),
            };
        }
    }

    private const string ToolUseResponse = """
        {"id":"msg_1","type":"message","role":"assistant","model":"claude-sonnet-5-5",
         "content":[{"type":"tool_use","id":"toolu_abc","name":"get_stock","input":{"sku":"HDPH-01"}}],
         "stop_reason":"tool_use","stop_sequence":null,
         "usage":{"input_tokens":12,"output_tokens":7}}
        """;

    private const string FinalResponse = """
        {"id":"msg_2","type":"message","role":"assistant","model":"claude-sonnet-5-5",
         "content":[{"type":"text","text":"There are 42 units in stock."}],
         "stop_reason":"end_turn","stop_sequence":null,
         "usage":{"input_tokens":30,"output_tokens":9}}
        """;

    private static AgentSettings Settings() => new()
    {
        LlmProvider = "anthropic",
        AnthropicApiKey = "sk-ant-test-not-a-real-key",
        LlmModel = AgentSettings.DefaultAnthropicModel,
    };

    [Fact]
    public async Task ToolUseRoundTrip_SendsToolResultAndMapsUsage()
    {
        var handler = new StubHandler(ToolUseResponse, FinalResponse);
        var toolCalls = new List<string>();
        var stock = AIFunctionFactory.Create(
            (string sku) =>
            {
                toolCalls.Add(sku);
                return "42";
            },
            name: "get_stock",
            description: "Units in stock for a SKU.");

        using var http = new HttpClient(handler);
        var client = ChatClientFactory.CreateAnthropicClient(Settings(), http)
            .AsBuilder().UseFunctionInvocation().Build();

        var response = await client.GetResponseAsync(
            "How many HDPH-01 are in stock?", new ChatOptions { Tools = [stock] });

        // The tool the model "asked for" ran, with the model's arguments.
        toolCalls.Should().Equal("HDPH-01");
        response.Text.Should().Be("There are 42 units in stock.");

        handler.Seen.Should().HaveCount(2);

        // First request: Messages API shape.
        var (first, firstBody) = handler.Seen[0];
        first.Method.Should().Be(HttpMethod.Post);
        first.RequestUri!.AbsolutePath.Should().EndWith("/v1/messages");
        // The SDK documents ApiKey as sent in "Authorization: Bearer" (ClientOptions.ApiKey).
        first.Headers.Authorization!.ToString().Should().Be("Bearer sk-ant-test-not-a-real-key");
        using var firstJson = JsonDocument.Parse(firstBody);
        firstJson.RootElement.GetProperty("model").GetString().Should().Be("claude-sonnet-5-5");
        firstJson.RootElement.GetProperty("max_tokens").GetInt32().Should().Be(ChatClientFactory.AnthropicDefaultMaxOutputTokens);
        firstJson.RootElement.GetProperty("tools")[0].GetProperty("name").GetString().Should().Be("get_stock");

        // Follow-up request: the assistant's tool_use is echoed and a tool_result answers the same id.
        using var secondJson = JsonDocument.Parse(handler.Seen[1].Body);
        var messages = secondJson.RootElement.GetProperty("messages").EnumerateArray().ToList();
        var blocks = messages.SelectMany(m => m.GetProperty("content").ValueKind == JsonValueKind.Array
            ? m.GetProperty("content").EnumerateArray()
            : Enumerable.Empty<JsonElement>()).ToList();
        blocks.Should().Contain(b => b.GetProperty("type").GetString() == "tool_use"
                                     && b.GetProperty("id").GetString() == "toolu_abc");
        var toolResult = blocks.Single(b => b.GetProperty("type").GetString() == "tool_result");
        toolResult.GetProperty("tool_use_id").GetString().Should().Be("toolu_abc");
        toolResult.GetRawText().Should().Contain("42");

        // Usage is summed across both model calls (12 + 30 in, 7 + 9 out).
        response.Usage.Should().NotBeNull();
        response.Usage!.InputTokenCount.Should().Be(42);
        response.Usage.OutputTokenCount.Should().Be(16);
    }

    [Fact]
    public void Create_RoutesTheAnthropicProviderToTheAnthropicAdapter()
    {
        var client = ChatClientFactory.Create(Settings());
        var meta = client.GetService<ChatClientMetadata>();
        meta!.DefaultModelId.Should().Be("claude-sonnet-5-5");
        meta.ProviderName.Should().NotBeNullOrWhiteSpace();
    }

    [Fact]
    public void MissingKey_NamesTheVariableAndNeverTheValue()
    {
        var act = () => ChatClientFactory.Create(Settings() with { AnthropicApiKey = " " });
        act.Should().Throw<InvalidOperationException>().WithMessage("ANTHROPIC_API_KEY is required*");

        var validate = () => AgentSettingsValidator.Validate(
            Settings() with { AnthropicApiKey = "", Environment = "development" },
            Microsoft.Extensions.Logging.Abstractions.NullLogger.Instance);
        validate.Should().Throw<InvalidOperationException>().WithMessage("ANTHROPIC_API_KEY is required*");
    }

    [Fact]
    public void Settings_DefaultToSonnetAndPreferAnthropicModelOverLlmModel()
    {
        var env = new Dictionary<string, string?>
        {
            ["LLM_PROVIDER"] = "anthropic",
            ["ANTHROPIC_API_KEY"] = "k",
            ["LLM_MODEL"] = "gpt-4.1", // what the compose files forward by default
        };
        var config = new ConfigurationBuilder().AddInMemoryCollection(env).Build();

        var settings = AgentSettingsLoader.Load(config);
        settings.LlmModel.Should().Be("claude-sonnet-5-5");

        env["ANTHROPIC_MODEL"] = "claude-haiku-5-5";
        AgentSettingsLoader.Load(new ConfigurationBuilder().AddInMemoryCollection(env).Build())
            .LlmModel.Should().Be("claude-haiku-5-5");
    }

    [Fact]
    public async Task EmbeddingsAreUnavailableWithAClearMessageNotACrash()
    {
        var provider = EmbeddingClientFactory.CreateProvider(Settings());
        var act = () => provider.EmbedAsVectorLiteralAsync("x");
        await act.Should().ThrowAsync<InvalidOperationException>().WithMessage("*no embeddings API*");
    }
}
