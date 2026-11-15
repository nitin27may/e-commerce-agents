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

    [Theory]
    [InlineData("x\r\ny", "x\ny")]
    [InlineData("x\ry", "x\ny")]
    [InlineData("a\n\nb", "a\n\nb")]
    [InlineData("\n", "\n")]
    public async Task StreamAsync_RoundTripsSseFramingFrame_NormalisingCarriageReturns(string sent, string expected)
    {
        // #80: a "\r" inside a chunk must be split into data lines by the writer,
        // not embedded raw, where a spec-compliant reader treats it as a line end.
        var handler = new SseResponseHandler(SseFraming.Frame(sent) + SseFraming.Frame("[DONE]"));
        var client = BuildClient(handler);

        var deltas = await CollectAsync(client.StreamAsync("product-discovery", "http://localhost", "hi"));

        deltas.Should().Equal(expected);
    }

    [Fact]
    public void SseFraming_SplitsOnEveryLineBreakStyle()
    {
        SseFraming.Frame("x\r\ny\rz\nw", "delta").Should().Be("event: delta\ndata: x\ndata: y\ndata: z\ndata: w\n\n");
        SseFraming.Frame("hello").Should().Be("data: hello\n\n");
    }

    private sealed class DroppedStreamHandler(string head) : HttpMessageHandler
    {
        public List<string?> Paths { get; } = [];

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct)
        {
            Paths.Add(request.RequestUri?.AbsolutePath);
            if (request.RequestUri?.AbsolutePath == "/message:send")
            {
                return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
                {
                    Content = new StringContent("{\"response\":\"SHOULD NOT BE USED\"}", Encoding.UTF8, "application/json"),
                });
            }
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StreamContent(new DroppingStream(Encoding.UTF8.GetBytes(head))),
            });
        }
    }

    /// <summary>Serves its bytes, then throws like a dropped connection.</summary>
    private sealed class DroppingStream(byte[] head) : Stream
    {
        private int _pos;
        public override bool CanRead => true;
        public override bool CanSeek => false;
        public override bool CanWrite => false;
        public override long Length => throw new NotSupportedException();
        public override long Position { get => _pos; set => throw new NotSupportedException(); }
        public override void Flush() { }
        public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
        public override void SetLength(long value) => throw new NotSupportedException();
        public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();

        public override int Read(byte[] buffer, int offset, int count)
        {
            if (_pos >= head.Length)
            {
                throw new IOException("connection dropped");
            }
            var n = Math.Min(count, head.Length - _pos);
            Array.Copy(head, _pos, buffer, offset, n);
            _pos += n;
            return n;
        }

        public override ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken ct = default) =>
            ValueTask.FromResult(ReadInto(buffer.Span));

        private int ReadInto(Span<byte> buffer)
        {
            if (_pos >= head.Length)
            {
                throw new IOException("connection dropped");
            }
            var n = Math.Min(buffer.Length, head.Length - _pos);
            head.AsSpan(_pos, n).CopyTo(buffer);
            _pos += n;
            return n;
        }
    }

    [Fact]
    public async Task StreamAsync_DropAfterFirstFrame_KeepsPartialTextAddsNoteAndNeverReposts()
    {
        var handler = new DroppedStreamHandler("data: partial\n\n");
        var client = BuildClient(handler);

        var deltas = await CollectAsync(client.StreamAsync("product-discovery", "http://localhost", "hi"));

        deltas.Should().HaveCount(2);
        deltas[0].Should().Be("partial");
        deltas[1].Should().Contain("interrupted");
        handler.Paths.Should().Equal("/message:stream");
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
