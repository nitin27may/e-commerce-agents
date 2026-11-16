using System.Net.Http.Json;
using System.Text;
using System.Text.Json;
using ECommerceAgents.Shared.A2A;
using ECommerceAgents.Shared.A2A.Protocol;
using ECommerceAgents.Shared.Agents;
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
/// A specialist's replay miss, over both wires, against a real in-memory .NET specialist whose model has no recording:
/// the specialist reports it as a miss (legacy: 424 and an <c>event: replay_miss</c> frame, as the Python specialist
/// does; A2A 1.0: a failed task carrying <c>replay-miss/v1</c>), and the orchestrator's client raises
/// <see cref="SpecialistReplayMissException"/> instead of returning an apology the model would answer.
/// </summary>
public sealed class A2AReplayMissTests
{
    private static OrchestratorClient Client(HttpMessageHandler handler, string transport, string provider = "replay")
    {
        A2AProtocolClient.ForgetCards();
        var settings = new AgentSettings
        {
            // Only a caller that is itself replaying honours a miss (ReplayMiss.IsActive).
            LlmProvider = provider,
            AgentSharedSecret = ScriptedSpecialist.Secret,
            JwtSecret = new string('j', 48),
            Environment = "test",
            A2aTransport = transport,
        };
        return new OrchestratorClient(
            new HttpClient(handler) { Timeout = TimeSpan.FromSeconds(30) },
            settings,
            new AuthServerClient(new HttpClient(), settings),
            NullLogger<OrchestratorClient>.Instance);
    }

    [Fact]
    public void ARealMissAndASpecialistsMiss_AreBothRecognised_WhateverWrapsThem()
    {
        ReplayMiss.IsMiss(new ReplayChatClient.FixtureMissingException("k", "/f")).Should().BeTrue();
        ReplayMiss.IsMiss(new SpecialistReplayMissException()).Should().BeTrue();
        ReplayMiss.IsMiss(new InvalidOperationException("tool failed", new ReplayChatClient.FixtureMissingException("k", "/f"))).Should().BeTrue();
        ReplayMiss.IsMiss(new AggregateException(new SpecialistReplayMissException())).Should().BeTrue();
        ReplayMiss.IsMiss(new InvalidOperationException("other")).Should().BeFalse();
        ReplayMiss.IsMiss(null).Should().BeFalse();
    }

    [Fact]
    public async Task TheLegacySpecialistRoutes_ReportAMissAsTheMarkerNotAnError()
    {
        await using var specialist = await ScriptedSpecialist.StartAsync(mode: "miss");
        using var http = specialist.App.GetTestClient();
        foreach (var h in ScriptedSpecialist.CallerHeaders())
        {
            http.DefaultRequestHeaders.Add(h.Key, h.Value);
        }

        var blocking = await http.PostAsJsonAsync("/message:send", new { message = "hi" });
        ((int)blocking.StatusCode).Should().Be(424);
        (await blocking.Content.ReadAsStringAsync()).Should().Be("""{"error":"replay_miss"}""");

        var streamed = await http.PostAsJsonAsync("/message:stream", new { message = "hi" });
        ((int)streamed.StatusCode).Should().Be(200);
        var body = await streamed.Content.ReadAsStringAsync();
        body.Should().Be("event: replay_miss\ndata: {\"error\":\"replay_miss\"}\n\ndata: [DONE]\n\n");
        body.Should().NotContain("abc123", "the missed request's hash never leaves the process");
    }

    [Theory]
    [InlineData("legacy")]
    [InlineData("a2a")]
    public async Task Blocking_AMissRaisesInsteadOfReturningAnApology(string transport)
    {
        await using var specialist = await ScriptedSpecialist.StartAsync(mode: "miss");
        var client = Client(specialist.App.GetTestServer().CreateHandler(), transport);
        using var scope = Ambient.Scope(ScriptedSpecialist.User, "customer", ScriptedSpecialist.Session);

        var act = () => client.SendAsync("product-discovery", "http://localhost", "hi");

        await act.Should().ThrowAsync<SpecialistReplayMissException>();
    }

    [Theory]
    [InlineData("legacy")]
    [InlineData("a2a")]
    public async Task Streaming_AMissRaisesInsteadOfYieldingAnApology(string transport)
    {
        await using var specialist = await ScriptedSpecialist.StartAsync(mode: "miss");
        var client = Client(specialist.App.GetTestServer().CreateHandler(), transport);
        using var scope = Ambient.Scope(ScriptedSpecialist.User, "customer", ScriptedSpecialist.Session);

        var act = async () =>
        {
            await foreach (var _ in client.StreamAsync("product-discovery", "http://localhost", "hi"))
            {
            }
        };

        await act.Should().ThrowAsync<SpecialistReplayMissException>();
    }

    [Theory]
    [InlineData("legacy")]
    [InlineData("a2a")]
    public async Task Blocking_ANonReplayCaller_TreatsASpecialistsMissAsAnOrdinaryFailure(string transport)
    {
        await using var specialist = await ScriptedSpecialist.StartAsync(mode: "miss");
        var client = Client(specialist.App.GetTestServer().CreateHandler(), transport, provider: "openai");
        using var scope = Ambient.Scope(ScriptedSpecialist.User, "customer", ScriptedSpecialist.Session);

        var text = await client.SendAsync("product-discovery", "http://localhost", "hi");

        text.Should().Contain("returned an error").And.NotContain("replay");
    }

    [Theory]
    [InlineData("legacy")]
    [InlineData("a2a")]
    public async Task Streaming_ANonReplayCaller_NeverRaisesAMiss_AndNeverShowsTheMarkerAsText(string transport)
    {
        await using var specialist = await ScriptedSpecialist.StartAsync(mode: "miss");
        var client = Client(specialist.App.GetTestServer().CreateHandler(), transport, provider: "openai");
        using var scope = Ambient.Scope(ScriptedSpecialist.User, "customer", ScriptedSpecialist.Session);
        var text = new StringBuilder();

        await foreach (var delta in client.StreamAsync("product-discovery", "http://localhost", "hi"))
        {
            text.Append(delta);
        }

        text.ToString().Should().NotContain("replay_miss");
    }

    [Theory]
    [InlineData("legacy")]
    [InlineData("a2a")]
    public async Task AFailureThatIsNotAMiss_IsStillAnApologyNotAnException(string transport)
    {
        await using var specialist = await ScriptedSpecialist.StartAsync(mode: "boom");
        var client = Client(specialist.App.GetTestServer().CreateHandler(), transport);
        using var scope = Ambient.Scope(ScriptedSpecialist.User, "customer", ScriptedSpecialist.Session);

        var text = await client.SendAsync("product-discovery", "http://localhost", "hi");

        text.Should().Contain("returned an error");
    }
}
