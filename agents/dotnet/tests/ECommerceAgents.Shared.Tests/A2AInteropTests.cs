using System.Threading.Channels;
using A2A;
using ECommerceAgents.Shared.A2A.Protocol;
using ECommerceAgents.Shared.Auth;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using Xunit;
using Ambient = ECommerceAgents.Shared.Context.RequestContext;
using OrchestratorClient = ECommerceAgents.Shared.A2A.A2AClient;

namespace ECommerceAgents.Shared.Tests;

/// <summary>Skips a fact unless an environment variable names a running peer (a test that needs another process).</summary>
public sealed class RequiresEnvFactAttribute : FactAttribute
{
    public RequiresEnvFactAttribute(string variable)
    {
        if (string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable(variable)))
        {
            Skip = $"set {variable} to a running peer; scripts/a2a-interop.sh starts one";
        }
    }
}

/// <summary>Skips a theory unless an environment variable names a running peer.</summary>
public sealed class RequiresEnvTheoryAttribute : TheoryAttribute
{
    public RequiresEnvTheoryAttribute(string variable)
    {
        if (string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable(variable)))
        {
            Skip = $"set {variable} to a running peer; scripts/a2a-interop.sh starts one";
        }
    }
}

/// <summary>
/// B-13 interop, .NET client to Python specialist: <see cref="OrchestratorClient"/> with <c>A2A_TRANSPORT=a2a</c>
/// and the official A2A .NET SDK client, against a real Python specialist (a real process on a real socket, around
/// a scripted model: no key, no database). Skipped unless <c>PYTHON_SPECIALIST_URL</c> is set;
/// <c>scripts/a2a-interop.sh</c> starts the host (<c>agents/python/tests/a2a_interop_server.py</c>) and runs this.
/// The other direction (the Python client against a .NET specialist) is
/// <c>agents/python/tests/test_a2a_dotnet_interop.py</c>.
/// </summary>
public sealed class A2AInteropTests
{
    private const string UrlVar = "PYTHON_SPECIALIST_URL";
    private const string FailVar = "PYTHON_SPECIALIST_URL_FAIL";
    private const string MissVar = "PYTHON_SPECIALIST_URL_MISS";
    private const string Session = "6f1c0a5e-2d3b-4c7e-9a10-1b2c3d4e5f60";
    private const string User = "alice@example.com";

    private static string Url(string variable) => Environment.GetEnvironmentVariable(variable)!.TrimEnd('/');

    // The scripted Python host's fixed credential (tests/a2a_support.py).
    private static string Secret => Environment.GetEnvironmentVariable("PYTHON_SPECIALIST_SECRET") ?? "conformance-secret";

    private static AgentSettings Settings() => new()
    {
        AgentSharedSecret = Secret,
        JwtSecret = new string('j', 48),
        Environment = "test",
        A2aTransport = "a2a",
    };

    private static OrchestratorClient Client()
    {
        A2AProtocolClient.ForgetCards();
        return new OrchestratorClient(
            new HttpClient(new SocketsHttpHandler { AllowAutoRedirect = false }) { Timeout = TimeSpan.FromSeconds(30) },
            Settings(),
            new AuthServerClient(new HttpClient(), Settings()),
            NullLogger<OrchestratorClient>.Instance);
    }

    private static HttpClient Caller(bool extensions = true)
    {
        var http = new HttpClient(new SocketsHttpHandler { AllowAutoRedirect = false });
        http.DefaultRequestHeaders.Add("X-Agent-Secret", Secret);
        http.DefaultRequestHeaders.Add("X-User-Email", User);
        http.DefaultRequestHeaders.Add("X-User-Role", "customer");
        if (extensions)
        {
            http.DefaultRequestHeaders.Add("A2A-Extensions", string.Join(", ", A2AWire.AllExtensionUris));
        }

        return http;
    }

    private static SendMessageRequest Request(string? contextId = null) => new()
    {
        Message = new Message
        {
            Role = Role.User,
            MessageId = Guid.NewGuid().ToString("N"),
            Parts = [Part.FromText("price of the XM5?")],
            ContextId = contextId,
        },
    };

    // ─────────────────────── the official .NET SDK client against a Python specialist ───────────────────────

    [RequiresEnvFact(UrlVar)]
    public async Task ThePythonCard_ParsesWithTheOfficialDotNetResolver()
    {
        var card = await new A2ACardResolver(new Uri(Url(UrlVar) + "/"), new HttpClient()).GetAgentCardAsync();

        card.Name.Should().Be("product-discovery");
        card.Capabilities.Streaming.Should().BeTrue();
        card.SupportedInterfaces.Should().ContainSingle().Which.ProtocolBinding.Should().Be("JSONRPC");
        card.SupportedInterfaces[0].ProtocolVersion.Should().Be("1.0");
        card.Capabilities.Extensions!.Select(e => e.Uri).Should().BeEquivalentTo(A2AWire.AllExtensionUris);
        card.Skills.Select(s => s.Id).Should().Contain(["lookup", "stock"]);
    }

    [RequiresEnvFact(UrlVar)]
    public async Task ABlockingTask_FromPython_CompletesWithTheAnswerStepsAndUsage()
    {
        var card = await new A2ACardResolver(new Uri(Url(UrlVar) + "/"), new HttpClient()).GetAgentCardAsync();
        var sdk = A2AClientFactory.Create(card, Caller());

        var response = await sdk.SendMessageAsync(Request(Session));

        var task = response.Task!;
        task.Status.State.Should().Be(TaskState.Completed);
        task.ContextId.Should().Be(Session);
        string.Concat(task.Artifacts!.SelectMany(a => a.Parts).Select(p => p.Text)).Should().Be("Hello world");
        var steps = new List<A2AWire.WireStep>();
        A2AWire.MergeSteps(steps, task.Metadata![A2AWire.StepsExtensionUri]).Should().ContainSingle();
        steps[0].ToolName.Should().Be("lookup");
        steps[0].Agent.Should().Be("product-discovery");
        A2AWire.ParseUsage(task.Metadata[A2AWire.UsageExtensionUri])!.InputTokenCount.Should().Be(5);
    }

    [RequiresEnvFact(UrlVar)]
    public async Task AStreamedTask_FromPython_WalksTheLifecycle_WithAppendChunks()
    {
        var card = await new A2ACardResolver(new Uri(Url(UrlVar) + "/"), new HttpClient()).GetAgentCardAsync();
        var sdk = A2AClientFactory.Create(card, Caller());

        var events = new List<StreamResponse>();
        await foreach (var e in sdk.SendStreamingMessageAsync(Request()))
        {
            events.Add(e);
        }

        events[0].Task!.Status.State.Should().Be(TaskState.Submitted);
        var states = events.Where(e => e.StatusUpdate is not null).Select(e => e.StatusUpdate!.Status.State).ToList();
        states[0].Should().Be(TaskState.Working);
        states[^1].Should().Be(TaskState.Completed);
        var chunks = events.Where(e => e.ArtifactUpdate is not null).Select(e => e.ArtifactUpdate!).ToList();
        chunks.Should().HaveCountGreaterThan(1);
        chunks[0].Append.Should().BeFalse();
        chunks.Skip(1).Should().OnlyContain(c => c.Append);
        chunks[^1].LastChunk.Should().BeTrue();
        string.Concat(chunks.SelectMany(c => c.Artifact.Parts).Select(p => p.Text)).Should().Be("Hello world");
    }

    // ─────────────────────── the orchestrator's client (A2A_TRANSPORT=a2a) against a Python specialist ───────────────────────

    [RequiresEnvFact(UrlVar)]
    public async Task TheOrchestratorsBlockingCall_ReadsAPythonSpecialist()
    {
        var client = Client();
        using var scope = Ambient.Scope(User, "customer", Session);

        var text = await client.SendAsync("product-discovery", Url(UrlVar), "price of the XM5?");

        text.Should().Be("Hello world");
        var step = Ambient.CurrentSteps.Should().ContainSingle().Subject;
        step.ToolName.Should().Be("lookup");
        step.Agent.Should().Be("product-discovery");
        step.ToolOutput!.ToString().Should().Contain("XM5=79.99 for alice@example.com");
        Ambient.CurrentRunUsage.ByAgent["product-discovery"].OutputTokenCount.Should().Be(2);
    }

    [RequiresEnvFact(UrlVar)]
    public async Task TheOrchestratorsStreamingCall_ReadsAPythonSpecialist_AndForwardsStepsLive()
    {
        var client = Client();
        var channel = Channel.CreateUnbounded<StreamFrame>();
        using var scope = Ambient.Scope(User, "customer", Session);
        using var stream = Ambient.StreamScope(channel.Writer);

        var deltas = new List<string>();
        await foreach (var d in client.StreamAsync("product-discovery", Url(UrlVar), "price of the XM5?"))
        {
            deltas.Add(d);
        }

        string.Concat(deltas).Should().Be("Hello world");
        Ambient.CurrentSteps.Should().ContainSingle().Which.ToolName.Should().Be("lookup");
        channel.Writer.Complete();
        var frames = await channel.Reader.ReadAllAsync().ToListAsync();
        frames.Should().ContainSingle(f => f.Event == "step").Which.Data.Should().Contain("\"tool_name\":\"lookup\"");
        Ambient.CurrentRunUsage.ByAgent["product-discovery"].InputTokenCount.Should().Be(5);
    }

    [RequiresEnvFact(FailVar)]
    public async Task APythonAgentError_IsAFailedTask_WithoutTheErrorText()
    {
        var client = Client();
        using var scope = Ambient.Scope(User, "customer", "");

        var text = await client.SendAsync("product-discovery", Url(FailVar), "hi");

        text.Should().Be("The product-discovery agent returned an error (task failed). Please try again.");
        text.Should().NotContain("secret_table");
    }

    [RequiresEnvFact(MissVar)]
    public async Task APythonReplayMiss_IsRecognisedOnTheWire()
    {
        A2AProtocolClient.ForgetCards();
        var protocol = new A2AProtocolClient(
            new HttpClient(new SocketsHttpHandler { AllowAutoRedirect = false }),
            (request, _, _) =>
            {
                request.Headers.TryAddWithoutValidation("X-Agent-Secret", Secret);
                request.Headers.TryAddWithoutValidation("X-User-Email", User);
                request.Headers.TryAddWithoutValidation("X-User-Role", "customer");
                return Task.CompletedTask;
            },
            NullLogger.Instance);

        var reply = await protocol.SendBlockingAsync("product-discovery", Url(MissVar), "hi");

        reply.State.Should().Be("failed");
        reply.ReplayMiss.Should().BeTrue();
    }

    [RequiresEnvTheory(UrlVar)]
    [InlineData("SendMessage")]
    [InlineData("SendStreamingMessage")]
    public async Task ThePythonServer_EchoesActivatedExtensions_InTheHeaderTheDotNetServerWrites(string method)
    {
        string[] listed = [A2AWire.UsageExtensionUri, "https://example.com/unknown/v1", A2AWire.StepsExtensionUri, A2AWire.UsageExtensionUri];
        using var http = Caller(extensions: false);
        using var request = new HttpRequestMessage(HttpMethod.Post, Url(UrlVar) + "/a2a/v1")
        {
            Content = new StringContent(
                "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"" + method + "\",\"params\":{\"message\":{\"messageId\":\"m1\",\"role\":\"ROLE_USER\",\"parts\":[{\"text\":\"hi\"}]}}}",
                System.Text.Encoding.UTF8, "application/json"),
        };
        request.Headers.Add("A2A-Version", "1.0");
        request.Headers.Add("A2A-Extensions", string.Join(", ", listed));

        using var response = await http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead);

        response.EnsureSuccessStatusCode();
        // The SDK fixes no order for the echo; the set and the format are what both servers share.
        string.Join(", ", response.Headers.GetValues("A2A-Extensions")).Split(", ")
            .Should().BeEquivalentTo([A2AWire.UsageExtensionUri, A2AWire.StepsExtensionUri]);
    }

    // The orchestrator's own client, on either wire, against a Python specialist whose model has no recording: it raises
    // the miss (the chat route reports it) instead of returning an apology the orchestrator's model would answer.
    [RequiresEnvTheory(MissVar)]
    [InlineData("legacy")]
    [InlineData("a2a")]
    public async Task APythonReplayMiss_RaisesFromTheOrchestratorsClient_BlockingAndStreaming(string transport)
    {
        A2AProtocolClient.ForgetCards();
        var settings = Settings() with { A2aTransport = transport };
        var client = new OrchestratorClient(
            new HttpClient(new SocketsHttpHandler { AllowAutoRedirect = false }) { Timeout = TimeSpan.FromSeconds(30) },
            settings,
            new AuthServerClient(new HttpClient(), settings),
            NullLogger<OrchestratorClient>.Instance);
        using var scope = Ambient.Scope(User, "customer", Session);

        var blocking = () => client.SendAsync("product-discovery", Url(MissVar), "hi");
        await blocking.Should().ThrowAsync<ECommerceAgents.Shared.A2A.SpecialistReplayMissException>();

        var streaming = async () =>
        {
            await foreach (var _ in client.StreamAsync("product-discovery", Url(MissVar), "hi"))
            {
            }
        };
        await streaming.Should().ThrowAsync<ECommerceAgents.Shared.A2A.SpecialistReplayMissException>();
    }

    // ─────────────────────── B-14: the signed user-context token, minted here and verified by Python ───────────────────────

    private const string TokenVar = "PYTHON_SPECIALIST_URL_TOKEN";

    private static OrchestratorClient ClientWithIdentity(string identity, string transport)
    {
        A2AProtocolClient.ForgetCards();
        var settings = new AgentSettings
        {
            AgentSharedSecret = Secret,
            JwtSecret = new string('j', 48),
            Environment = "test",
            A2aTransport = transport,
            A2aIdentity = identity,
        };
        return new OrchestratorClient(
            new HttpClient(new SocketsHttpHandler { AllowAutoRedirect = false }),
            settings,
            new AuthServerClient(new HttpClient(), settings),
            NullLogger<OrchestratorClient>.Instance);
    }

    [RequiresEnvFact(TokenVar)]
    public async Task APythonTokenModeSpecialist_ServesTheUserADotNetTokenNames_OnBothWires()
    {
        foreach (var transport in new[] { "a2a", "legacy" })
        {
            var client = ClientWithIdentity("token", transport);
            using var scope = Ambient.Scope("dora@example.com", "customer", "");

            var text = await client.SendAsync("product-discovery", Url(TokenVar), "price of the XM5?");

            text.Should().Be("Hello world", transport);
            // No X-User-* header was sent: the tool knows who is asking only from the token.
            Ambient.CurrentSteps.Should().ContainSingle().Which.ToolOutput!.ToString().Should().Contain("for dora@example.com", transport);
        }
    }

    [RequiresEnvFact(TokenVar)]
    public async Task APythonTokenModeSpecialist_RefusesAHeadersOnlyDotNetCaller_OnBothWires()
    {
        foreach (var transport in new[] { "a2a", "legacy" })
        {
            var client = ClientWithIdentity("headers", transport);
            using var scope = Ambient.Scope("dora@example.com", "customer", "");

            var text = await client.SendAsync("product-discovery", Url(TokenVar), "hi");

            text.Should().Be("The product-discovery agent returned an error (status 401). Please try again.", transport);
        }
    }

    [RequiresEnvFact(TokenVar)]
    public async Task APythonSpecialist_RefusesATokenMintedForAnotherSpecialist()
    {
        var client = ClientWithIdentity("token", "legacy");
        using var scope = Ambient.Scope("dora@example.com", "customer", "");

        // The caller names order-management (and so mints a token for it) but the URL is product-discovery's.
        var text = await client.SendAsync("order-management", Url(TokenVar), "hi");

        text.Should().Be("The order-management agent returned an error (status 401). Please try again.");
    }
}
