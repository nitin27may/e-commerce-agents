using ECommerceAgents.Shared.A2A;
using ECommerceAgents.Shared.Auth;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging.Abstractions;
using System.Net;
using System.Text;
using System.Text.Json;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// #97 / E-2b — a specialist's token usage reaches the orchestrator's accounting
/// on the .NET stack, in the same wire shape the Python stack uses, and never
/// enters the text the orchestrator's model reads.
/// </summary>
public sealed class SpecialistUsageTests
{
    // The wire shape, as literal JSON. The Python tests assert the same string
    // (agents/python/tests/test_specialist_usage.py::WIRE_USAGE_JSON), so a
    // Python orchestrator and .NET specialists (and the reverse) interoperate.
    // Change both or neither.
    private const string WireJson =
        "{\"input_token_count\":120,\"output_token_count\":30,\"cached_input_token_count\":100,\"model\":\"gpt-4.1\"}";

    private static readonly SpecialistUsage Wire = new(120, 30, 100, "gpt-4.1");
    private const string SpecialistText = "The Sony WH-1000XM5 is $79.99.";

    private static UsageDetails MeaiUsage() => new()
    {
        InputTokenCount = 120,
        OutputTokenCount = 30,
        TotalTokenCount = 150,
        CachedInputTokenCount = 100,
    };

    // ───────────── wire shape ─────────────

    [Fact]
    public void WireShape_IsTheSharedLiteral()
    {
        Wire.ToWireJson().Should().Be(WireJson);
        SpecialistUsage.From(MeaiUsage(), "gpt-4.1").Should().Be(Wire);
        JsonSerializer.Deserialize<SpecialistUsage>(WireJson).Should().Be(Wire);
    }

    [Fact]
    public void From_ReturnsNullWhenNoTokensWereReported()
    {
        SpecialistUsage.From(null, "m").Should().BeNull();
        SpecialistUsage.From(new UsageDetails(), "m").Should().BeNull();
    }

    [Fact]
    public void AgentResponse_SerializesUsageBesideTheResponse_AndOmitsItWhenAbsent()
    {
        var web = new JsonSerializerOptions(JsonSerializerDefaults.Web);

        var with = JsonSerializer.Serialize(new AgentHost.AgentResponse(SpecialistText, null, Wire), web);
        using (var doc = JsonDocument.Parse(with))
        {
            doc.RootElement.GetProperty("response").GetString().Should().Be(SpecialistText);
            doc.RootElement.GetProperty("usage").GetRawText().Should().Be(WireJson);
        }

        JsonSerializer.Serialize(new AgentHost.AgentResponse(SpecialistText), web).Should().NotContain("usage");
    }

    [Fact]
    public void RunUsage_SumsPerAgentAndInTotal()
    {
        var acc = new RunUsage();
        acc.Add("orchestrator", new SpecialistUsage(1000, 50, 0, "gpt-4.1"));
        acc.Add("product-discovery", Wire);
        acc.Add("product-discovery", Wire);
        acc.Add("order-management", null);

        acc.ByAgent.Keys.Should().BeEquivalentTo("orchestrator", "product-discovery");
        acc.ByAgent["product-discovery"].InputTokenCount.Should().Be(240);
        acc.Total().Should().Be(new SpecialistUsage(1240, 110, 200, "gpt-4.1"));
    }

    // ───────────── the specialist host side ─────────────

    private static IServiceProvider Services(FakeChatClient chat)
    {
        var services = new ServiceCollection();
        services.AddSingleton(new AgentSettings { LlmModel = "gpt-4.1" });
        // RunAgentWithHistoryAsync also resolves a pool; it is only touched when no
        // history is forwarded, which these tests always do.
        services.AddSingleton(new DatabasePool(new AgentSettings { DatabaseUrl = "Host=unused" }));
        services.AddSingleton<AIAgent>(chat.AsAIAgent(instructions: "i", name: "specialist"));
        return services.BuildServiceProvider();
    }

    [Fact]
    public async Task Specialist_RecordsItsOwnUsage_BlockingAndStreaming()
    {
        var blocking = new FakeChatClient { Usage = MeaiUsage() }.EnqueueResponse(SpecialistText);
        using (RequestContext.Scope("u@example.com", "customer", "", new List<HistoryEntry> { new("user", "hi") }))
        {
            var text = await AgentHost.RunAgentWithHistoryAsync(Services(blocking), "q");

            text.Should().Be(SpecialistText);
            RequestContext.CurrentRunUsage.Total().Should().Be(Wire);
        }

        var streaming = new FakeChatClient { Usage = MeaiUsage() }.EnqueueResponse(SpecialistText);
        using (RequestContext.Scope("u@example.com", "customer", "", new List<HistoryEntry> { new("user", "hi") }))
        {
            var chunks = new List<string>();
            await foreach (var c in AgentHost.RunAgentWithHistoryStreamingAsync(Services(streaming), "q"))
            {
                chunks.Add(c);
            }

            // The usage item is not text, so it never reaches the stream's text frames.
            chunks.Should().Equal(SpecialistText);
            RequestContext.CurrentRunUsage.Total().Should().Be(Wire);
        }
    }

    // ───────────── the orchestrator side (A2AClient) ─────────────

    private static A2AClient BuildClient(HttpMessageHandler handler)
    {
        var settings = new AgentSettings
        {
            AgentSharedSecret = new string('s', 48),
            JwtSecret = new string('j', 48),
            Environment = "test",
        };
        return new A2AClient(
            new HttpClient(handler) { BaseAddress = new Uri("http://localhost") },
            settings,
            new AuthServerClient(new HttpClient(), settings),
            NullLogger<A2AClient>.Instance);
    }

    private sealed class Canned(string body, string contentType) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct) =>
            Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent(body, Encoding.UTF8, contentType),
            });
    }

    [Fact]
    public async Task SendAsync_RecordsUsage_AndTheReturnedTextCarriesNone()
    {
        var body = $"{{\"response\":\"{SpecialistText}\",\"steps\":[],\"usage\":{WireJson}}}";
        using var scope = RequestContext.Scope("u@example.com", "customer", "");

        var text = await BuildClient(new Canned(body, "application/json"))
            .SendAsync("product-discovery", "http://localhost", "hi");

        text.Should().Be(SpecialistText);
        RequestContext.CurrentRunUsage.ByAgent["product-discovery"].Should().Be(Wire);
    }

    [Theory]
    [InlineData("\"lots of tokens\"")]
    [InlineData("[1,2,3]")]
    [InlineData("42")]
    [InlineData("{\"input_token_count\":\"many\",\"output_token_count\":1,\"cached_input_token_count\":0,\"model\":\"m\"}")]
    public async Task SendAsync_AMalformedUsageField_DoesNotHideTheAnswer(string badUsage)
    {
        var body = $"{{\"response\":\"{SpecialistText}\",\"usage\":{badUsage}}}";
        using var scope = RequestContext.Scope("u@example.com", "customer", "");

        var text = await BuildClient(new Canned(body, "application/json"))
            .SendAsync("product-discovery", "http://localhost", "hi");

        text.Should().Be(SpecialistText, "the specialist ran and replied; only its accounting was bad");
        RequestContext.CurrentRunUsage.ByAgent.Should().BeEmpty();
    }

    [Fact]
    public async Task StreamAsync_RecordsUsageFrame_AndNeverYieldsIt()
    {
        var sse = "data: The Sony WH-\n\ndata: 1000XM5 is $79.99.\n\n"
            + $"event: usage\ndata: {WireJson}\n\ndata: [DONE]\n\n";
        using var scope = RequestContext.Scope("u@example.com", "customer", "");

        var yielded = new List<string>();
        await foreach (var d in BuildClient(new Canned(sse, "text/event-stream"))
            .StreamAsync("product-discovery", "http://localhost", "hi"))
        {
            yielded.Add(d);
        }

        string.Concat(yielded).Should().Be("The Sony WH-1000XM5 is $79.99.");
        string.Concat(yielded).Should().NotContain("token").And.NotContain("gpt-4.1");
        RequestContext.CurrentRunUsage.ByAgent["product-discovery"].Should().Be(Wire);
    }

    [Fact]
    public async Task StreamAsync_ToleratesAMalformedUsageFrame()
    {
        var sse = "data: hi\n\nevent: usage\ndata: {not json\n\ndata: [DONE]\n\n";
        using var scope = RequestContext.Scope("u@example.com", "customer", "");

        var yielded = new List<string>();
        await foreach (var d in BuildClient(new Canned(sse, "text/event-stream"))
            .StreamAsync("product-discovery", "http://localhost", "hi"))
        {
            yielded.Add(d);
        }

        yielded.Should().Equal("hi");
        RequestContext.CurrentRunUsage.Total().Should().BeNull();
    }
}
