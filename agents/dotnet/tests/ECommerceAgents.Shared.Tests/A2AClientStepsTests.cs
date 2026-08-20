using ECommerceAgents.Shared.A2A;
using ECommerceAgents.Shared.Auth;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using System.Net;
using System.Text;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// <see cref="A2AClient.SendAsync"/> / <see cref="A2AClient.StreamAsync"/>
/// merging a specialist's returned agentic-timeline steps into
/// <see cref="RequestContext.CurrentSteps"/>, tagged with the specialist's
/// name (issue #16) — the orchestrator-side half of cross-process step
/// capture. <c>AgentHost.cs</c>'s side (a specialist returning its own
/// steps) has no equivalent unit test: it's assembled inline in a Minimal
/// API route lambda, not a separately-callable method, same gap the #14
/// streaming-wiring tests already accepted.
/// </summary>
public sealed class A2AClientStepsTests
{
    private static AgentSettings DefaultSettings() => new()
    {
        AgentSharedSecret = new string('s', 48),
        JwtSecret = new string('j', 48),
        Environment = "test",
    };

    private static A2AClient BuildClient(HttpMessageHandler handler) =>
        new(
            new HttpClient(handler) { BaseAddress = new Uri("http://localhost") },
            DefaultSettings(),
            new AuthServerClient(new HttpClient(), DefaultSettings()),
            NullLogger<A2AClient>.Instance
        );

    private sealed class StaticResponseHandler(string body, string contentType) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct) =>
            Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent(body, Encoding.UTF8, contentType),
            });
    }

    [Fact]
    public async Task SendAsync_MergesReturnedSteps_TaggedWithTheSpecialistsName()
    {
        var handler = new StaticResponseHandler(
            """{"response":"here are some headphones","steps":[{"toolName":"SearchProducts","toolInput":null,"toolOutput":null,"status":"success","durationMs":12}]}""",
            "application/json"
        );
        var client = BuildClient(handler);
        using var scope = RequestContext.Scope("u@example.com", "customer", "sess-1");

        await client.SendAsync("product-discovery", "http://localhost", "find headphones");

        RequestContext.CurrentSteps.Should().ContainSingle();
        var step = RequestContext.CurrentSteps[0];
        step.ToolName.Should().Be("SearchProducts");
        step.Agent.Should().Be("product-discovery");
    }

    [Fact]
    public async Task SendAsync_NoStepsInResponse_LeavesCurrentStepsEmpty()
    {
        var handler = new StaticResponseHandler("""{"response":"ok"}""", "application/json");
        var client = BuildClient(handler);
        using var scope = RequestContext.Scope("u@example.com", "customer", "sess-1");

        await client.SendAsync("product-discovery", "http://localhost", "hi");

        RequestContext.CurrentSteps.Should().BeEmpty();
    }

    [Fact]
    public async Task StreamAsync_MergesStepsFromTheEventStepsFrame_TaggedWithTheSpecialistsName()
    {
        var sse = string.Join(
            "\n",
            "data: Wireless headphones",
            "",
            "event: steps",
            "data: [{\"toolName\":\"SearchProducts\",\"toolInput\":null,\"toolOutput\":null,\"status\":\"success\",\"durationMs\":8}]",
            "",
            "data: [DONE]",
            ""
        );
        var handler = new StaticResponseHandler(sse, "text/event-stream");
        var client = BuildClient(handler);
        using var scope = RequestContext.Scope("u@example.com", "customer", "sess-1");

        var deltas = new List<string>();
        await foreach (var delta in client.StreamAsync("product-discovery", "http://localhost", "hi"))
        {
            deltas.Add(delta);
        }

        // The steps frame must not itself surface as a text delta.
        deltas.Should().Equal("Wireless headphones");
        RequestContext.CurrentSteps.Should().ContainSingle();
        RequestContext.CurrentSteps[0].Agent.Should().Be("product-discovery");
        RequestContext.CurrentSteps[0].ToolName.Should().Be("SearchProducts");
    }

    [Fact]
    public async Task StreamAsync_MultipleStepsInOneFrame_AllMergedInOrder()
    {
        var sse = string.Join(
            "\n",
            "event: steps",
            "data: [{\"toolName\":\"SearchProducts\",\"toolInput\":null,\"toolOutput\":null,\"status\":\"success\",\"durationMs\":8}," +
                "{\"toolName\":\"GetProductDetails\",\"toolInput\":null,\"toolOutput\":null,\"status\":\"success\",\"durationMs\":4}]",
            "",
            "data: [DONE]",
            ""
        );
        var handler = new StaticResponseHandler(sse, "text/event-stream");
        var client = BuildClient(handler);
        using var scope = RequestContext.Scope("u@example.com", "customer", "sess-1");

        await foreach (var _ in client.StreamAsync("product-discovery", "http://localhost", "hi"))
        {
            // drain
        }

        RequestContext.CurrentSteps.Select(s => s.ToolName).Should().Equal("SearchProducts", "GetProductDetails");
    }
}
