using ECommerceAgents.Shared.Auth;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using Microsoft.AspNetCore.TestHost;
using Microsoft.Extensions.Logging.Abstractions;
using Xunit;
using Ambient = ECommerceAgents.Shared.Context.RequestContext;
using OrchestratorClient = ECommerceAgents.Shared.A2A.A2AClient;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// B-14, the sending side: what <see cref="OrchestratorClient"/> puts on a call to a specialist under each
/// <c>A2A_IDENTITY</c> mode and each transport, and that a real specialist host in each mode accepts exactly what
/// the matching client sends: the token for the specialist's own name, the headers only where they are allowed.
/// </summary>
[Collection(nameof(ProcessEnvironmentCollection))]
public sealed class A2AClientIdentityTests
{
    private static AgentSettings Settings(string identity, string transport) => new()
    {
        AgentSharedSecret = ScriptedSpecialist.Secret,
        JwtSecret = new string('j', 48),
        Environment = "test",
        A2aTransport = transport,
        A2aIdentity = identity,
    };

    private sealed class SpyHandler(HttpMessageHandler inner) : DelegatingHandler(inner)
    {
        public List<HttpRequestMessage> Seen { get; } = [];

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct)
        {
            // The headers as sent; the message itself is disposed with the call.
            var copy = new HttpRequestMessage(request.Method, request.RequestUri);
            foreach (var (name, values) in request.Headers)
            {
                copy.Headers.TryAddWithoutValidation(name, values);
            }

            Seen.Add(copy);
            return base.SendAsync(request, ct);
        }
    }

    private static OrchestratorClient Client(HttpMessageHandler handler, string identity, string transport)
    {
        ECommerceAgents.Shared.A2A.Protocol.A2AProtocolClient.ForgetCards();
        var settings = Settings(identity, transport);
        return new OrchestratorClient(
            new HttpClient(handler), settings, new AuthServerClient(new HttpClient(), settings), NullLogger<OrchestratorClient>.Instance);
    }

    private static string? Header(HttpRequestMessage request, string name) =>
        request.Headers.TryGetValues(name, out var values) ? string.Join(",", values) : null;

    [Theory]
    [InlineData("a2a", "/a2a/v1")]
    [InlineData("legacy", "/message:send")]
    public async Task BothMode_SendsTheTokenAndTheHeaders(string transport, string path)
    {
        await using var specialist = await ScriptedSpecialist.StartAsync();
        var spy = new SpyHandler(specialist.App.GetTestServer().CreateHandler());
        using var scope = Ambient.Scope("alice@example.com", "customer", ScriptedSpecialist.Session);

        await Client(spy, "both", transport).SendAsync("product-discovery", "http://localhost", "hi");

        var call = spy.Seen.Single(r => r.RequestUri!.AbsolutePath == path);
        Header(call, "X-User-Email").Should().Be("alice@example.com");
        Header(call, "X-User-Role").Should().Be("customer");
        Header(call, "X-Session-Id").Should().Be(ScriptedSpecialist.Session);
        var verified = UserContextToken.Verify(
            Header(call, UserContextToken.HeaderName)!, "product-discovery", UserContextToken.DeriveKey(ScriptedSpecialist.Secret));
        (verified.Email, verified.Role, verified.SessionId).Should().Be(("alice@example.com", "customer", ScriptedSpecialist.Session));
    }

    [Theory]
    [InlineData("a2a", "/a2a/v1")]
    [InlineData("legacy", "/message:send")]
    public async Task TokenMode_SendsNoIdentityHeaders(string transport, string path)
    {
        await using var specialist = await ScriptedSpecialist.StartAsync(env: new Dictionary<string, string> { ["A2A_IDENTITY"] = "token" });
        var spy = new SpyHandler(specialist.App.GetTestServer().CreateHandler());
        using var scope = Ambient.Scope("alice@example.com", "customer", ScriptedSpecialist.Session);

        var text = await Client(spy, "token", transport).SendAsync("product-discovery", "http://localhost", "hi");

        text.Should().Be(ScriptedToolChatClient.Answer);
        var call = spy.Seen.Single(r => r.RequestUri!.AbsolutePath == path);
        Header(call, "X-User-Email").Should().BeNull();
        Header(call, "X-User-Role").Should().BeNull();
        Header(call, "X-Session-Id").Should().BeNull();
        Header(call, UserContextToken.HeaderName).Should().NotBeNull();
    }

    [Theory]
    [InlineData("a2a")]
    [InlineData("legacy")]
    public async Task HeadersMode_SendsNoToken(string transport)
    {
        await using var specialist = await ScriptedSpecialist.StartAsync();
        var spy = new SpyHandler(specialist.App.GetTestServer().CreateHandler());
        using var scope = Ambient.Scope("alice@example.com", "customer", "");

        await Client(spy, "headers", transport).SendAsync("product-discovery", "http://localhost", "hi");

        spy.Seen.Should().OnlyContain(r => Header(r, UserContextToken.HeaderName) == null);
        spy.Seen.Where(r => r.RequestUri!.AbsolutePath != "/.well-known/agent-card.json")
            .Should().OnlyContain(r => Header(r, "X-User-Email") == "alice@example.com");
    }

    [Theory]
    [InlineData("both", "a2a")]
    [InlineData("both", "legacy")]
    [InlineData("token", "a2a")]
    [InlineData("token", "legacy")]
    public async Task ASpecialistInTheSameMode_ServesTheUserTheTokenNames(string identity, string transport)
    {
        await using var specialist = await ScriptedSpecialist.StartAsync(env: new Dictionary<string, string> { ["A2A_IDENTITY"] = identity });
        using var scope = Ambient.Scope("carol@example.com", "customer", "");
        var client = Client(specialist.App.GetTestServer().CreateHandler(), identity, transport);

        var text = await client.SendAsync("product-discovery", "http://localhost", "hi");

        text.Should().Be(ScriptedToolChatClient.Answer);
        // The tool ran as the user the token named, not as "system" (in token mode no header names anyone).
        Ambient.CurrentSteps.Should().ContainSingle().Which.ToolOutput!.ToString().Should().Contain("for carol@example.com");
    }

    [Theory]
    [InlineData("a2a")]
    [InlineData("legacy")]
    public async Task ATokenModeSpecialist_RefusesAHeadersOnlyCaller(string transport)
    {
        await using var specialist = await ScriptedSpecialist.StartAsync(env: new Dictionary<string, string> { ["A2A_IDENTITY"] = "token" });
        using var scope = Ambient.Scope("alice@example.com", "customer", "");
        var client = Client(specialist.App.GetTestServer().CreateHandler(), "headers", transport);

        var text = await client.SendAsync("product-discovery", "http://localhost", "hi");

        text.Should().Be("The product-discovery agent returned an error (status 401). Please try again.");
        specialist.Model.Started.Should().Be(0, "the call was refused before any agent ran");
    }

    [Fact]
    public async Task ABothModeSpecialist_StillServesAnOldHeadersOnlyOrchestrator()
    {
        await using var specialist = await ScriptedSpecialist.StartAsync();
        using var scope = Ambient.Scope("alice@example.com", "customer", "");
        var client = Client(specialist.App.GetTestServer().CreateHandler(), "headers", "legacy");

        (await client.SendAsync("product-discovery", "http://localhost", "hi")).Should().Be(ScriptedToolChatClient.Answer);
    }

    [Fact]
    public async Task TheTokenIsBoundToTheSpecialistItNames()
    {
        await using var specialist = await ScriptedSpecialist.StartAsync(env: new Dictionary<string, string> { ["A2A_IDENTITY"] = "token" });
        using var scope = Ambient.Scope("alice@example.com", "customer", "");
        var client = Client(specialist.App.GetTestServer().CreateHandler(), "token", "legacy");

        // The orchestrator asks for the order-management specialist (and so mints a token for it), but the URL it
        // is sent to is the product-discovery host: the token is for another audience, so the host refuses it.
        var text = await client.SendAsync("order-management", "http://localhost", "hi");

        text.Should().Be("The order-management agent returned an error (status 401). Please try again.");
    }
}
