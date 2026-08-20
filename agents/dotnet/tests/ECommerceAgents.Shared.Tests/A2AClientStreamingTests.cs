using ECommerceAgents.Shared.A2A;
using ECommerceAgents.Shared.Auth;
using ECommerceAgents.Shared.Configuration;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using System.Net;
using System.Text;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// <see cref="A2AClient.StreamAsync"/> — consumes a specialist's
/// <c>/message:stream</c> SSE response (issue #14). No real network call:
/// the HTTP transport is stubbed to hand back a canned SSE body.
/// </summary>
public sealed class A2AClientStreamingTests
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

    private sealed class SseResponseHandler(string sseBody, HttpStatusCode status = HttpStatusCode.OK) : HttpMessageHandler
    {
        public string? RequestedPath { get; private set; }

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct)
        {
            RequestedPath = request.RequestUri?.AbsolutePath;
            return Task.FromResult(new HttpResponseMessage(status)
            {
                Content = new StringContent(sseBody, Encoding.UTF8, "text/event-stream"),
            });
        }
    }

    private static async Task<List<string>> CollectAsync(IAsyncEnumerable<string> deltas)
    {
        var result = new List<string>();
        await foreach (var d in deltas)
        {
            result.Add(d);
        }
        return result;
    }

    [Fact]
    public async Task StreamAsync_YieldsEachDataFrameAndStopsBeforeDone()
    {
        var handler = new SseResponseHandler("data: Hel\n\ndata: lo\n\ndata: [DONE]\n\n");
        var client = BuildClient(handler);

        var deltas = await CollectAsync(client.StreamAsync("product-discovery", "http://localhost", "hi"));

        deltas.Should().Equal("Hel", "lo");
        handler.RequestedPath.Should().Be("/message:stream");
    }

    [Fact]
    public async Task StreamAsync_ReassemblesMultiLineDataFramesWithEmbeddedNewlines()
    {
        // Mirrors ChatRoutes.StreamAsync's own per-line SSE framing (spec §9.2.6):
        // a chunk containing a real newline arrives as multiple consecutive
        // "data: <line>" lines within the same event, joined with "\n".
        var handler = new SseResponseHandler("data: line one\ndata: line two\n\ndata: [DONE]\n\n");
        var client = BuildClient(handler);

        var deltas = await CollectAsync(client.StreamAsync("product-discovery", "http://localhost", "hi"));

        deltas.Should().Equal("line one\nline two");
    }

    [Fact]
    public async Task StreamAsync_NonSuccessStatus_YieldsFallbackMessageAndStops()
    {
        var handler = new SseResponseHandler("irrelevant", HttpStatusCode.InternalServerError);
        var client = BuildClient(handler);

        var deltas = await CollectAsync(client.StreamAsync("product-discovery", "http://localhost", "hi"));

        deltas.Should().ContainSingle().Which.Should().Contain("status 500");
    }

    [Fact]
    public async Task StreamAsync_EmptyBody_YieldsNothing()
    {
        var handler = new SseResponseHandler(string.Empty);
        var client = BuildClient(handler);

        var deltas = await CollectAsync(client.StreamAsync("product-discovery", "http://localhost", "hi"));

        deltas.Should().BeEmpty();
    }
}
