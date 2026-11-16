using System.Net;
using System.Text;
using System.Text.Json;
using System.Threading.Channels;
using A2A;
using OrchestratorClient = ECommerceAgents.Shared.A2A.A2AClient;
using Ambient = ECommerceAgents.Shared.Context.RequestContext;
using ECommerceAgents.Shared.A2A.Protocol;
using ECommerceAgents.Shared.Auth;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using Microsoft.AspNetCore.TestHost;
using Microsoft.Extensions.Logging.Abstractions;
using Xunit;
using static ECommerceAgents.Shared.Tests.A2ASupport;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// B-13: <see cref="A2AClient"/> with <c>A2A_TRANSPORT=a2a</c>. Against a real in-memory .NET specialist (the
/// round trip), and against a scripted wire for the rules a well-behaved specialist never exercises: only the
/// registry origin ever receives a request, redirects are never followed, an abandoned stream is cancelled, and
/// the retry-only-if-it-never-ran rule.
/// </summary>
public sealed class A2AClientTransportTests
{
    private const string Registry = "http://specialist";

    private static AgentSettings Settings(string transport = "a2a") => new()
    {
        AgentSharedSecret = ScriptedSpecialist.Secret,
        JwtSecret = new string('j', 48),
        Environment = "test",
        A2aTransport = transport,
    };

    private static OrchestratorClient Client(HttpMessageHandler handler, string transport = "a2a")
    {
        A2AProtocolClient.ForgetCards();
        return new OrchestratorClient(
            new HttpClient(handler) { Timeout = TimeSpan.FromSeconds(30) },
            Settings(transport),
            new AuthServerClient(new HttpClient(), Settings(transport)),
            NullLogger<OrchestratorClient>.Instance);
    }

    private static async Task<List<string>> CollectAsync(IAsyncEnumerable<string> deltas)
    {
        var list = new List<string>();
        await foreach (var d in deltas)
        {
            list.Add(d);
        }

        return list;
    }

    // ─────────────────────── round trip against a real .NET specialist ───────────────────────

    [Fact]
    public async Task Blocking_RoundTripsAgainstARealSpecialist_WithStepsAndUsage()
    {
        await using var specialist = await ScriptedSpecialist.StartAsync();
        var client = Client(specialist.App.GetTestServer().CreateHandler());
        using var scope = Ambient.Scope(ScriptedSpecialist.User, "customer", ScriptedSpecialist.Session);

        var text = await client.SendAsync("product-discovery", "http://localhost", "price of the XM5?");

        text.Should().Be(ScriptedToolChatClient.Answer);
        var step = Ambient.CurrentSteps.Should().ContainSingle().Subject;
        step.ToolName.Should().Be("lookup");
        step.Agent.Should().Be("product-discovery");
        step.ToolOutput!.ToString().Should().Contain("XM5=79.99 for alice@example.com");
        var usage = Ambient.CurrentRunUsage.ByAgent["product-discovery"];
        (usage.InputTokenCount, usage.OutputTokenCount).Should().Be((5L, 2L));
    }

    [Fact]
    public async Task Streaming_RoundTripsAgainstARealSpecialist_StepsGoToTheBrowserLive()
    {
        await using var specialist = await ScriptedSpecialist.StartAsync();
        var client = Client(specialist.App.GetTestServer().CreateHandler());
        var channel = Channel.CreateUnbounded<StreamFrame>();
        using var scope = Ambient.Scope(ScriptedSpecialist.User, "customer", ScriptedSpecialist.Session);
        using var stream = Ambient.StreamScope(channel.Writer);

        var deltas = await CollectAsync(client.StreamAsync("product-discovery", "http://localhost", "price of the XM5?"));

        string.Concat(deltas).Should().Be(ScriptedToolChatClient.Answer);
        Ambient.CurrentSteps.Should().ContainSingle().Which.ToolName.Should().Be("lookup");
        channel.Writer.Complete();
        var frames = new List<StreamFrame>();
        await foreach (var f in channel.Reader.ReadAllAsync())
        {
            frames.Add(f);
        }

        var stepFrame = frames.Should().ContainSingle(f => f.Event == "step").Subject;
        using var json = JsonDocument.Parse(stepFrame.Data);
        json.RootElement.GetProperty("tool_name").GetString().Should().Be("lookup");
        json.RootElement.GetProperty("agent").GetString().Should().Be("product-discovery");
        Ambient.CurrentRunUsage.ByAgent["product-discovery"].InputTokenCount.Should().Be(5);
    }

    [Fact]
    public async Task TheSessionTravelsAsContextId_AndTheCredentialsAsHeaders()
    {
        await using var specialist = await ScriptedSpecialist.StartAsync();
        var spy = new RecordingHandler(specialist.App.GetTestServer().CreateHandler());
        var client = Client(spy);
        using var scope = Ambient.Scope("bob@example.com", "seller", ScriptedSpecialist.Session);

        await client.SendAsync("product-discovery", "http://localhost", "hi");

        var rpc = spy.Requests.Single(r => r.Path == "/a2a/v1");
        rpc.Body.Should().Contain($"\"contextId\":\"{ScriptedSpecialist.Session}\"");
        rpc.Headers["X-Agent-Secret"].Should().Be(ScriptedSpecialist.Secret);
        rpc.Headers["X-User-Email"].Should().Be("bob@example.com");
        rpc.Headers["X-User-Role"].Should().Be("seller");
        rpc.Headers["A2A-Version"].Should().Be("1.0");
        rpc.Headers["A2A-Extensions"].Should().Be(string.Join(", ", A2AWire.AllExtensionUris));
    }

    [Fact]
    public async Task NoSession_MeansNoContextId()
    {
        await using var specialist = await ScriptedSpecialist.StartAsync();
        var spy = new RecordingHandler(specialist.App.GetTestServer().CreateHandler());
        var client = Client(spy);
        using var scope = Ambient.Scope(ScriptedSpecialist.User, "customer", "");

        await client.SendAsync("product-discovery", "http://localhost", "hi");

        spy.Requests.Single(r => r.Path == "/a2a/v1").Body.Should().NotContain("contextId");
    }

    [Fact]
    public async Task TheLegacyTransport_IsTheDefault_AndStillUsesMessageSend()
    {
        await using var specialist = await ScriptedSpecialist.StartAsync();
        var spy = new RecordingHandler(specialist.App.GetTestServer().CreateHandler());
        var client = Client(spy, transport: "legacy");
        using var scope = Ambient.Scope(ScriptedSpecialist.User, "customer", "");

        (await client.SendAsync("product-discovery", "http://localhost", "hi")).Should().Be(ScriptedToolChatClient.Answer);

        spy.Requests.Select(r => r.Path).Should().Equal("/message:send");
        new AgentSettings().A2aTransport.Should().Be("legacy");
    }

    // ─────────────────────── only the registry origin, no redirects ───────────────────────

    [Fact]
    public async Task ACardNamingAnotherOrigin_IsRefused_AndNothingIsSentThere()
    {
        var wire = new ScriptedWire { CardUrl = "http://evil.example/a2a/v1" };
        var client = Client(wire);
        using var scope = Ambient.Scope(ScriptedSpecialist.User, "customer", ScriptedSpecialist.Session);

        var blocking = await client.SendAsync("product-discovery", Registry, "hi");
        var streamed = await CollectAsync(client.StreamAsync("product-discovery", Registry, "hi"));

        blocking.Should().Be("Failed to reach the product-discovery agent. Please try again later.");
        streamed.Should().ContainSingle().Which.Should().Be("Failed to reach the product-discovery agent. Please try again later.");
        wire.Hosts.Should().OnlyContain(h => h == "specialist", "no request, and no credential, ever left the registry origin");
        wire.Calls.Should().NotContain(c => c.StartsWith("rpc:", StringComparison.Ordinal), "no message was sent");
    }

    [Fact]
    public async Task ACardWithAnotherPort_IsAlsoAForeignOrigin()
    {
        var wire = new ScriptedWire { CardUrl = "http://specialist:9999/a2a/v1" };
        var client = Client(wire);

        (await client.SendAsync("product-discovery", Registry, "hi")).Should().StartWith("Failed to reach");

        wire.Calls.Should().NotContain(c => c.StartsWith("rpc:", StringComparison.Ordinal));
    }

    [Theory]
    [InlineData("http://specialist", "http://specialist:80/a2a/v1")]
    [InlineData("http://SPECIALIST:8081/", "http://specialist:8081/a2a/v1")]
    [InlineData("https://specialist", "https://specialist:443/a2a/v1")]
    public void SameOrigin_IgnoresDefaultPortsAndCase(string registry, string url)
    {
        var card = new AgentCard { SupportedInterfaces = [new AgentInterface { Url = url, ProtocolBinding = "JSONRPC", ProtocolVersion = "1.0" }] };

        A2AProtocolClient.ForeignInterface(card, registry).Should().BeNull();
    }

    [Fact]
    public async Task ARedirectIsNeverFollowed_AndCarriesNoCredentialsAway()
    {
        var wire = new ScriptedWire { CardRedirectTo = "http://evil.example/.well-known/agent-card.json" };
        var client = Client(wire);

        (await client.SendAsync("product-discovery", Registry, "hi")).Should().StartWith("The product-discovery agent returned an error (status 302)");

        wire.Hosts.Should().OnlyContain(h => h == "specialist");
    }

    [Fact]
    public async Task ARpcRedirect_IsRefused_Too()
    {
        var wire = new ScriptedWire { RpcRedirectTo = "http://evil.example/a2a/v1" };
        var client = Client(wire);

        (await client.SendAsync("product-discovery", Registry, "hi")).Should().StartWith("The product-discovery agent returned an error (status 307)");

        wire.Hosts.Should().OnlyContain(h => h == "specialist");
    }

    [Fact]
    public async Task ACardWithNoA2AInterface_IsACardProblem_NamingTheWayOut()
    {
        var wire = new ScriptedWire { StubCard = true };
        var client = Client(wire);

        (await client.SendAsync("product-discovery", Registry, "hi")).Should().StartWith("Failed to reach");

        wire.Calls.Should().Equal("card");
        var ex = await Assert.ThrowsAsync<A2ACardProblemException>(() => new A2AProtocolClient(
            new HttpClient(wire), (_, _) => Task.CompletedTask, NullLogger.Instance).SendBlockingAsync(Registry, "hi"));
        ex.Message.Should().Contain("A2A_TRANSPORT=legacy");
    }

    // ─────────────────────── the retry rule ───────────────────────

    [Fact]
    public async Task AStreamThatNeverRan_IsReSentOverTheBlockingCall_Once()
    {
        var wire = new ScriptedWire { StreamFailsWith = StreamFailure.Refused503 };
        var client = Client(wire);
        using var scope = Ambient.Scope(ScriptedSpecialist.User, "customer", "");

        var deltas = await CollectAsync(client.StreamAsync("product-discovery", Registry, "hi"));

        deltas.Should().Equal("blocking answer");
        wire.Calls.Where(c => c.StartsWith("rpc:", StringComparison.Ordinal)).Should().Equal("rpc:SendStreamingMessage", "rpc:SendMessage");
    }

    [Fact]
    public async Task AConnectionNeverMade_IsReSent()
    {
        var wire = new ScriptedWire { StreamFailsWith = StreamFailure.ConnectError };
        var client = Client(wire);
        using var scope = Ambient.Scope(ScriptedSpecialist.User, "customer", "");

        (await CollectAsync(client.StreamAsync("product-discovery", Registry, "hi"))).Should().Equal("blocking answer");
    }

    [Theory]
    [InlineData(StreamFailure.Gateway502)]
    [InlineData(StreamFailure.Gateway504)]
    [InlineData(StreamFailure.ReadTimeout)]
    [InlineData(StreamFailure.RpcError)]
    public async Task AFailureThatMayHaveRun_IsNeverReSent(StreamFailure failure)
    {
        var wire = new ScriptedWire { StreamFailsWith = failure };
        var client = Client(wire);
        using var scope = Ambient.Scope(ScriptedSpecialist.User, "customer", "");

        var deltas = await CollectAsync(client.StreamAsync("product-discovery", Registry, "hi"));

        deltas.Should().ContainSingle().Which.Should().Contain("could not be confirmed to have completed");
        wire.Calls.Should().NotContain("rpc:SendMessage", "re-sending could run its tools twice");
    }

    [Fact]
    public async Task AFailureAfterAFrame_KeepsThePartialText_AndNeverReSends()
    {
        var wire = new ScriptedWire { StreamFailsWith = StreamFailure.DropsAfterFirstChunk };
        var client = Client(wire);
        using var scope = Ambient.Scope(ScriptedSpecialist.User, "customer", "");

        var deltas = await CollectAsync(client.StreamAsync("product-discovery", Registry, "hi"));

        deltas.Should().Equal("Hel", "[The product-discovery agent's response was interrupted and may be incomplete.]");
        wire.Calls.Should().NotContain("rpc:SendMessage");
    }

    [Fact]
    public void NeverRan_Classification()
    {
        A2AProtocolClient.NeverRan(new HttpRequestException("x", null, HttpStatusCode.Unauthorized)).Should().BeTrue();
        A2AProtocolClient.NeverRan(new HttpRequestException("x", null, HttpStatusCode.BadGateway)).Should().BeFalse();
        A2AProtocolClient.NeverRan(new HttpRequestException(HttpRequestError.ConnectionError, "x")).Should().BeTrue();
        A2AProtocolClient.NeverRan(new HttpRequestException(HttpRequestError.ResponseEnded, "x")).Should().BeFalse();
        A2AProtocolClient.NeverRan(new A2ACardProblemException("x")).Should().BeTrue();
        A2AProtocolClient.NeverRan(new A2AException("x", A2AErrorCode.InternalError)).Should().BeFalse();
        A2AProtocolClient.NeverRan(new TimeoutException()).Should().BeFalse();
        A2AProtocolClient.NeverRan(new IOException("dropped")).Should().BeFalse();
    }

    // ─────────────────────── cancelling an abandoned stream ───────────────────────

    [Fact]
    public async Task AnAbandonedStream_SendsABestEffortCancelTask()
    {
        var wire = new ScriptedWire { StreamFailsWith = StreamFailure.None, HoldOpenAfterFirstChunk = true };
        var protocol = new A2AProtocolClient(new HttpClient(wire), (_, _) => Task.CompletedTask, NullLogger.Instance);
        A2AProtocolClient.ForgetCards();

        await foreach (var ev in protocol.StreamEventsAsync(Registry, "hi", TimeSpan.FromSeconds(30)))
        {
            if (ev.Kind == A2AEvent.Delta)
            {
                break; // the consumer walks away mid-stream
            }
        }

        wire.Calls.Should().Contain("rpc:CancelTask");
        wire.CancelledTaskId.Should().Be("task-1");
    }

    [Fact]
    public async Task AStreamThatFails_IsCancelled_AndAFailingCancelNeverReplacesTheOriginalError()
    {
        var wire = new ScriptedWire { StreamFailsWith = StreamFailure.DropsAfterFirstChunk, CancelFails = true };
        var protocol = new A2AProtocolClient(new HttpClient(wire), (_, _) => Task.CompletedTask, NullLogger.Instance);
        A2AProtocolClient.ForgetCards();

        var act = async () =>
        {
            await foreach (var _ in protocol.StreamEventsAsync(Registry, "hi", TimeSpan.FromSeconds(30)))
            {
            }
        };

        var thrown = await act.Should().ThrowAsync<Exception>();
        thrown.Which.Should().NotBeOfType<A2AException>("the cancel's own failure must not mask the stream's");
        wire.Calls.Should().Contain("rpc:CancelTask");
    }

    [Fact]
    public async Task ACompletedStream_SendsNoCancel()
    {
        var wire = new ScriptedWire { StreamFailsWith = StreamFailure.None };
        var protocol = new A2AProtocolClient(new HttpClient(wire), (_, _) => Task.CompletedTask, NullLogger.Instance);
        A2AProtocolClient.ForgetCards();

        var events = new List<A2AEvent>();
        await foreach (var ev in protocol.StreamEventsAsync(Registry, "hi", TimeSpan.FromSeconds(30)))
        {
            events.Add(ev);
        }

        string.Concat(events.Where(e => e.Kind == A2AEvent.Delta).Select(e => e.Text)).Should().Be("Hello");
        wire.Calls.Should().NotContain("rpc:CancelTask");
    }

    // ─────────────────────── a failed task ───────────────────────

    [Fact]
    public async Task AFailedTask_IsReportedAsAnError_NotAsAnswerText()
    {
        await using var specialist = await ScriptedSpecialist.StartAsync(mode: "boom");
        var client = Client(specialist.App.GetTestServer().CreateHandler());
        using var scope = Ambient.Scope(ScriptedSpecialist.User, "customer", "");

        var text = await client.SendAsync("product-discovery", "http://localhost", "hi");

        text.Should().Be("The product-discovery agent returned an error (task failed). Please try again.");
        text.Should().NotContain("secret_table");
    }

    [Fact]
    public async Task AReplayMiss_IsRecognisedOnTheWire()
    {
        await using var specialist = await ScriptedSpecialist.StartAsync(mode: "miss");
        var protocol = new A2AProtocolClient(
            specialist.App.GetTestServer().CreateClient(),
            (request, _) =>
            {
                foreach (var h in ScriptedSpecialist.CallerHeaders())
                {
                    request.Headers.TryAddWithoutValidation(h.Key, h.Value);
                }

                return Task.CompletedTask;
            },
            NullLogger.Instance);
        A2AProtocolClient.ForgetCards();

        var reply = await protocol.SendBlockingAsync("http://localhost", "hi");

        reply.State.Should().Be("failed");
        reply.ReplayMiss.Should().BeTrue();
    }

    // ─────────────────────── test doubles ───────────────────────

    private sealed record Seen(string Path, string Body, Dictionary<string, string> Headers);

    private sealed class RecordingHandler(HttpMessageHandler inner) : DelegatingHandler(inner)
    {
        public List<Seen> Requests { get; } = [];

        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            var body = request.Content is null ? string.Empty : await request.Content.ReadAsStringAsync(cancellationToken);
            Requests.Add(new Seen(
                request.RequestUri!.AbsolutePath,
                body,
                request.Headers.ToDictionary(h => h.Key, h => string.Join(", ", h.Value), StringComparer.OrdinalIgnoreCase)));
            return await base.SendAsync(request, cancellationToken);
        }
    }

    public enum StreamFailure
    {
        None,
        Refused503,
        Gateway502,
        Gateway504,
        ConnectError,
        ReadTimeout,
        RpcError,
        DropsAfterFirstChunk,
    }

    /// <summary>A scripted A2A 1.0 specialist wire: just enough JSON-RPC to drive the client's rules.</summary>
    private sealed class ScriptedWire : HttpMessageHandler
    {
        public string CardUrl { get; init; } = Registry + "/a2a/v1";
        public string? CardRedirectTo { get; init; }
        public string? RpcRedirectTo { get; init; }
        public bool StubCard { get; init; }
        public StreamFailure StreamFailsWith { get; init; }
        public bool HoldOpenAfterFirstChunk { get; init; }
        public bool CancelFails { get; init; }

        public List<string> Hosts { get; } = [];
        public List<string> Calls { get; } = [];
        public string? CancelledTaskId { get; private set; }

        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            Hosts.Add(request.RequestUri!.Host);
            if (request.RequestUri.AbsolutePath == "/.well-known/agent-card.json")
            {
                Calls.Add("card");
                if (CardRedirectTo is not null)
                {
                    return Redirect(HttpStatusCode.Found, CardRedirectTo);
                }

                var card = StubCard
                    ? "{\"name\":\"stub\",\"description\":\"d\",\"url\":\"http://0.0.0.0:8081\",\"capabilities\":[\"message:send\"],\"transport\":\"a2a\"}"
                    : SpecialistAgentCard.ToJson(CardWith(CardUrl));
                return Json(card);
            }

            if (RpcRedirectTo is not null && request.RequestUri.AbsolutePath == "/a2a/v1")
            {
                Calls.Add("rpc-redirect");
                return Redirect(HttpStatusCode.TemporaryRedirect, RpcRedirectTo);
            }

            using var doc = JsonDocument.Parse(await request.Content!.ReadAsStringAsync(cancellationToken));
            var method = doc.RootElement.GetProperty("method").GetString()!;
            var id = doc.RootElement.GetProperty("id").GetString()!;
            Calls.Add($"rpc:{method}");

            return method switch
            {
                "SendStreamingMessage" => await StreamAsync(id),
                "SendMessage" => Rpc(id, new SendMessageResponse { Task = Task("blocking answer", TaskState.Completed) }),
                "CancelTask" => CancelFails
                    ? throw new HttpRequestException("cancel refused", null, HttpStatusCode.ServiceUnavailable)
                    : CancelTask(id, doc.RootElement.GetProperty("params").GetProperty("id").GetString()!),
                _ => throw new InvalidOperationException(method),
            };
        }

        private static AgentCard CardWith(string url)
        {
            var registry = new A2ACustomOperationRegistryBuilder().Build();
            return SpecialistAgentCard.Build(null, "product-discovery", "d", new Uri(url).GetLeftPart(UriPartial.Authority), registry);
        }

        private static AgentTask Task(string text, TaskState state) => new()
        {
            Id = "task-1",
            ContextId = "ctx",
            Status = new global::A2A.TaskStatus { State = state },
            Artifacts = [new Artifact { ArtifactId = "a", Parts = [Part.FromText(text)] }],
        };

        private async Task<HttpResponseMessage> StreamAsync(string id)
        {
            switch (StreamFailsWith)
            {
                case StreamFailure.Refused503:
                    return new HttpResponseMessage(HttpStatusCode.ServiceUnavailable);
                case StreamFailure.Gateway502:
                    return new HttpResponseMessage(HttpStatusCode.BadGateway);
                case StreamFailure.Gateway504:
                    return new HttpResponseMessage(HttpStatusCode.GatewayTimeout);
                case StreamFailure.ConnectError:
                    throw new HttpRequestException(HttpRequestError.ConnectionError, "connection refused");
                case StreamFailure.ReadTimeout:
                    throw new TaskCanceledException("read timeout", new TimeoutException());
                case StreamFailure.RpcError:
                    return Json(JsonSerializer.Serialize(new { jsonrpc = "2.0", id, error = new { code = -32603, message = "Internal error" } }));
            }

            var frames = new StringBuilder();
            void Frame(StreamResponse r) =>
                frames.Append("data: ").Append(JsonSerializer.Serialize(
                    JsonRpcResponse.CreateJsonRpcResponse(new JsonRpcId(id), r), A2AJsonUtilities.DefaultOptions)).Append("\n\n");

            Frame(new StreamResponse { Task = new AgentTask { Id = "task-1", ContextId = "ctx", Status = new global::A2A.TaskStatus { State = TaskState.Submitted } } });
            Frame(new StreamResponse { ArtifactUpdate = new TaskArtifactUpdateEvent { TaskId = "task-1", ContextId = "ctx", Artifact = new Artifact { ArtifactId = "a", Parts = [Part.FromText("Hel")] } } });

            if (StreamFailsWith == StreamFailure.DropsAfterFirstChunk)
            {
                return Sse(new FaultingStream(frames.ToString()));
            }

            if (HoldOpenAfterFirstChunk)
            {
                return Sse(new FaultingStream(frames.ToString(), hang: true));
            }

            Frame(new StreamResponse { ArtifactUpdate = new TaskArtifactUpdateEvent { TaskId = "task-1", ContextId = "ctx", Append = true, LastChunk = true, Artifact = new Artifact { ArtifactId = "a", Parts = [Part.FromText("lo")] } } });
            Frame(new StreamResponse { StatusUpdate = new TaskStatusUpdateEvent { TaskId = "task-1", ContextId = "ctx", Status = new global::A2A.TaskStatus { State = TaskState.Completed } } });
            await global::System.Threading.Tasks.Task.CompletedTask;
            return Sse(new MemoryStream(Encoding.UTF8.GetBytes(frames.ToString())));
        }

        private HttpResponseMessage CancelTask(string id, string taskId)
        {
            CancelledTaskId = taskId;
            return Rpc(id, Task("x", TaskState.Canceled));
        }

        private static HttpResponseMessage Rpc<T>(string id, T result) =>
            Json(JsonSerializer.Serialize(JsonRpcResponse.CreateJsonRpcResponse(new JsonRpcId(id), result), A2AJsonUtilities.DefaultOptions));

        private static HttpResponseMessage Json(string body) =>
            new(HttpStatusCode.OK) { Content = new StringContent(body, Encoding.UTF8, "application/json") };

        private static HttpResponseMessage Sse(Stream body)
        {
            var content = new StreamContent(body);
            content.Headers.ContentType = new System.Net.Http.Headers.MediaTypeHeaderValue("text/event-stream");
            return new HttpResponseMessage(HttpStatusCode.OK) { Content = content };
        }

        private static HttpResponseMessage Redirect(HttpStatusCode status, string to)
        {
            var response = new HttpResponseMessage(status);
            response.Headers.Location = new Uri(to);
            return response;
        }
    }

    /// <summary>Serves some bytes, then either throws (a dropped connection) or hangs until cancelled.</summary>
    private sealed class FaultingStream(string prefix, bool hang = false) : Stream
    {
        private readonly MemoryStream _inner = new(Encoding.UTF8.GetBytes(prefix));

        public override async ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken cancellationToken = default)
        {
            var read = await _inner.ReadAsync(buffer, cancellationToken);
            if (read > 0)
            {
                return read;
            }

            if (hang)
            {
                await Task.Delay(Timeout.Infinite, cancellationToken);
            }

            throw new IOException("the connection was dropped");
        }

        public override int Read(byte[] buffer, int offset, int count) => throw new NotSupportedException();
        public override bool CanRead => true;
        public override bool CanSeek => false;
        public override bool CanWrite => false;
        public override long Length => throw new NotSupportedException();
        public override long Position { get => throw new NotSupportedException(); set => throw new NotSupportedException(); }
        public override void Flush() { }
        public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
        public override void SetLength(long value) => throw new NotSupportedException();
        public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();
    }
}
