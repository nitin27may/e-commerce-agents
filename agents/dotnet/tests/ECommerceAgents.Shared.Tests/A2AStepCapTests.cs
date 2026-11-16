using System.Net;
using System.Text;
using System.Text.Json;
using ECommerceAgents.Shared.A2A;
using ECommerceAgents.Shared.A2A.Protocol;
using ECommerceAgents.Shared.Auth;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using Xunit;
using Ambient = ECommerceAgents.Shared.Context.RequestContext;
using OrchestratorClient = ECommerceAgents.Shared.A2A.A2AClient;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// A specialist is not trusted to flood the orchestrator's timeline: one legacy frame (or blocking body) contributes at most
/// <see cref="OrchestratorClient.MaxStepsPerFrame"/> steps and one turn collects at most <see cref="OrchestratorClient.MaxStepsPerTurn"/>.
/// The twin of Python's <c>tests/test_legacy_wire_dotnet_shape.py</c> cap tests.
/// </summary>
public sealed class A2AStepCapTests
{
    private sealed class FixedHandler(string body, string contentType) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct) =>
            Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent(body, Encoding.UTF8, contentType) });
    }

    private static OrchestratorClient Client(HttpMessageHandler handler)
    {
        A2AProtocolClient.ForgetCards();
        var settings = new AgentSettings
        {
            AgentSharedSecret = ScriptedSpecialist.Secret,
            JwtSecret = new string('j', 48),
            Environment = "test",
            A2aTransport = "legacy",
        };
        return new OrchestratorClient(
            new HttpClient(handler),
            settings,
            new AuthServerClient(new HttpClient(), settings),
            NullLogger<OrchestratorClient>.Instance);
    }

    private static string StepsJson(int count) =>
        JsonSerializer.Serialize(Enumerable.Range(0, count).Select(i => new { toolName = $"t{i}", status = "success", durationMs = 1 }));

    [Fact]
    public async Task ABlockingBodyWithTooManySteps_ContributesOnlyTheFrameCap()
    {
        var body = $$"""{"response":"done","steps":{{StepsJson(500)}}}""";
        var client = Client(new FixedHandler(body, "application/json"));
        using var scope = Ambient.Scope(ScriptedSpecialist.User, "customer", ScriptedSpecialist.Session);

        var text = await client.SendAsync("product-discovery", "http://localhost", "hi");

        text.Should().Be("done");
        Ambient.CurrentSteps.Should().HaveCount(OrchestratorClient.MaxStepsPerFrame);
        Ambient.CurrentSteps.Select(s => s.ToolName).First().Should().Be("t0");
    }

    [Fact]
    public async Task AStreamOfManyStepFrames_StopsAtTheTurnCap()
    {
        var frames = new StringBuilder();
        for (var i = 0; i < 8; i++)
        {
            frames.Append("event: steps\ndata: ").Append(StepsJson(OrchestratorClient.MaxStepsPerFrame)).Append("\n\n");
        }

        frames.Append("data: ok\n\ndata: [DONE]\n\n");
        var client = Client(new FixedHandler(frames.ToString(), "text/event-stream"));
        using var scope = Ambient.Scope(ScriptedSpecialist.User, "customer", ScriptedSpecialist.Session);

        var text = new StringBuilder();
        await foreach (var delta in client.StreamAsync("product-discovery", "http://localhost", "hi"))
        {
            text.Append(delta);
        }

        text.ToString().Should().Be("ok");
        Ambient.CurrentSteps.Should().HaveCount(OrchestratorClient.MaxStepsPerTurn);
    }
}
