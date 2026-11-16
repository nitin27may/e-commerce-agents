using ECommerceAgents.Orchestrator.Modes;
using ECommerceAgents.Shared.A2A;
using ECommerceAgents.Shared.Auth;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Orchestration;
using ECommerceAgents.Shared.Prompts;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using Xunit;

namespace ECommerceAgents.Orchestrator.Tests;

/// <summary>
/// The model-backed orchestration modes end a turn on a replay miss by raising it, so the chat route can report it
/// (<see cref="ReplayMiss"/>) instead of returning the empty answer a swallowed workflow failure leaves behind.
/// </summary>
public sealed class ReplayMissModesTests : IDisposable
{
    private readonly string _fixtures = Directory.CreateTempSubdirectory("replay-empty").FullName;

    public void Dispose() => Directory.Delete(_fixtures, recursive: true);

    private static string RealPromptsRoot()
    {
        var dir = new DirectoryInfo(Directory.GetCurrentDirectory());
        while (dir is not null && !Directory.Exists(Path.Combine(dir.FullName, "agents", "python", "config", "prompts")))
        {
            dir = dir.Parent;
        }

        dir.Should().NotBeNull();
        return Path.Combine(dir!.FullName, "agents", "python", "config", "prompts");
    }

    private AgentSettings ReplaySettings() => new()
    {
        LlmProvider = "replay",
        ReplayFixturesDir = _fixtures,
        AgentSharedSecret = new string('s', 48),
        JwtSecret = new string('j', 48),
        AuthMode = "local",
        A2aTransport = "legacy",
        AgentRegistry = """{"product-discovery":"http://fake-product-discovery"}""",
    };

    [Fact]
    public async Task HandoffMode_AMissInTheTriageAgent_IsRaisedNotSwallowed()
    {
        var settings = ReplaySettings();
        var a2a = new A2AClient(new HttpClient(), settings, new AuthServerClient(new HttpClient(), settings), NullLogger<A2AClient>.Instance);
        var mode = new HandoffMode(settings, a2a, new PromptLoader(RealPromptsRoot()));

        var act = () => mode.RunAsync("anything unrecorded", new RunContext([], ""));

        var raised = await act.Should().ThrowAsync<Exception>();
        ReplayMiss.IsMiss(raised.Which).Should().BeTrue($"the mode raised {raised.Which.GetType().Name}: {raised.Which.Message}");
    }

    [Fact]
    public async Task GroupChatMode_AMissInAPanelist_IsRaisedNotSwallowed()
    {
        var settings = ReplaySettings();
        var mode = new GroupChatMode(settings, new PromptLoader(RealPromptsRoot()));

        var act = () => mode.RunAsync("anything unrecorded", new RunContext([], ""));

        var raised = await act.Should().ThrowAsync<Exception>();
        ReplayMiss.IsMiss(raised.Which).Should().BeTrue($"the mode raised {raised.Which.GetType().Name}: {raised.Which.Message}");
    }
}
