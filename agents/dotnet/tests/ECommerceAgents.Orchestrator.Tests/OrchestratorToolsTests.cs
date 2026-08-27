using ECommerceAgents.Orchestrator.Agent;
using ECommerceAgents.Shared.A2A;
using ECommerceAgents.Shared.Auth;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using System.Net;
using System.Net.Http.Json;
using System.Text;
using System.Threading.Channels;
using Xunit;

namespace ECommerceAgents.Orchestrator.Tests;

/// <summary>
/// <see cref="OrchestratorTools.CallSpecialistAgent"/>'s side effect on
/// <see cref="RequestContext.CurrentInvokedAgents"/> — backs the streaming
/// chat endpoint's dynamic <c>agents_involved</c> (mirrors Python's
/// <c>current_steps</c> capture, <c>routes.py:651-655</c>). No real network
/// call: the A2A HTTP call is stubbed.
/// </summary>
public sealed class OrchestratorToolsTests
{
    private sealed class StaticResponseHandler(string body) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct) =>
            Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent(body, Encoding.UTF8, "application/json"),
            });
    }

    private static OrchestratorTools BuildTools(string agentName = "product-discovery")
    {
        var settings = new AgentSettings
        {
            AgentSharedSecret = new string('s', 48),
            AuthMode = "local",
            AgentRegistry = $$"""{"{{agentName}}":"http://fake-{{agentName}}"}""",
        };
        var http = new HttpClient(new StaticResponseHandler("""{"response":"specialist reply"}"""));
        var a2a = new A2AClient(http, settings, new AuthServerClient(new HttpClient(), settings), NullLogger<A2AClient>.Instance);
        return new OrchestratorTools(a2a, settings, NullLogger<OrchestratorTools>.Instance);
    }

    [Fact]
    public async Task CallSpecialistAgent_RecordsInvocationOnRequestContext()
    {
        var tools = BuildTools("product-discovery");
        using var scope = RequestContext.Scope("alice@example.com", "customer", "sess-1");

        RequestContext.CurrentInvokedAgents.Should().BeEmpty();

        var reply = await tools.CallSpecialistAgent("product-discovery", "find headphones");

        reply.Should().Be("specialist reply");
        RequestContext.CurrentInvokedAgents.Should().ContainSingle().Which.Should().Be("product-discovery");
    }

    [Fact]
    public async Task CallSpecialistAgent_UnknownAgent_DoesNotRecordAndReturnsMessage()
    {
        var tools = BuildTools("product-discovery");
        using var scope = RequestContext.Scope("alice@example.com", "customer", "sess-1");

        var reply = await tools.CallSpecialistAgent("not-a-real-agent", "hi");

        reply.Should().Contain("Unknown agent");
        RequestContext.CurrentInvokedAgents.Should().BeEmpty();
    }

    [Fact]
    public async Task CallSpecialistAgent_MultipleCalls_RecordEachInvocation()
    {
        var tools = BuildTools("order-management");
        using var scope = RequestContext.Scope("alice@example.com", "customer", "sess-1");

        await tools.CallSpecialistAgent("order-management", "cancel order 123");
        await tools.CallSpecialistAgent("order-management", "what's the status");

        RequestContext.CurrentInvokedAgents.Should().Equal("order-management", "order-management");
    }

    // ─────────────────────── streaming forward (issue #14) ───────────

    private sealed class SseResponseHandler(string sseBody) : HttpMessageHandler
    {
        public string? RequestedPath { get; private set; }

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct)
        {
            RequestedPath = request.RequestUri?.AbsolutePath;
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent(sseBody, Encoding.UTF8, "text/event-stream"),
            });
        }
    }

    private static OrchestratorTools BuildStreamingTools(SseResponseHandler handler, string agentName = "product-discovery")
    {
        var settings = new AgentSettings
        {
            AgentSharedSecret = new string('s', 48),
            AuthMode = "local",
            AgentRegistry = $$"""{"{{agentName}}":"http://fake-{{agentName}}"}""",
        };
        var http = new HttpClient(handler);
        var a2a = new A2AClient(http, settings, new AuthServerClient(new HttpClient(), settings), NullLogger<A2AClient>.Instance);
        return new OrchestratorTools(a2a, settings, NullLogger<OrchestratorTools>.Instance);
    }

    [Fact]
    public async Task CallSpecialistAgent_WithNoStreamWriter_CallsMessageSendNotMessageStream()
    {
        // No RequestContext.StreamScope open (mirrors blocking /api/chat, or this
        // tool exercised directly, as every other test in this file does) — must
        // keep using the plain, non-streaming A2A endpoint exactly as before this
        // change.
        var tools = BuildTools("product-discovery");
        using var scope = RequestContext.Scope("alice@example.com", "customer", "sess-1");

        var reply = await tools.CallSpecialistAgent("product-discovery", "find headphones");

        reply.Should().Be("specialist reply");
    }

    [Fact]
    public async Task CallSpecialistAgent_WithStreamWriterOpen_UsesMessageStreamAndForwardsDeltas()
    {
        var handler = new SseResponseHandler("data: Wire\n\ndata: less headphones\n\ndata: [DONE]\n\n");
        var tools = BuildStreamingTools(handler);
        using var scope = RequestContext.Scope("alice@example.com", "customer", "sess-1");

        var channel = Channel.CreateUnbounded<StreamFrame>();
        using var streamScope = RequestContext.StreamScope(channel.Writer);

        var reply = await tools.CallSpecialistAgent("product-discovery", "find headphones");
        channel.Writer.Complete();

        var forwarded = new List<StreamFrame>();
        await foreach (var frame in channel.Reader.ReadAllAsync())
        {
            forwarded.Add(frame);
        }

        handler.RequestedPath.Should().Be("/message:stream");
        // The channel carries typed frames now, so a delta has to say it is one
        // — everything on it used to be emitted as `event: delta` by position.
        forwarded.Should().AllSatisfy(f => f.Event.Should().Be("delta"));
        forwarded.Select(f => f.Data).Should().Equal("Wire", "less headphones");
        reply.Should().Be("Wireless headphones");
    }
}
