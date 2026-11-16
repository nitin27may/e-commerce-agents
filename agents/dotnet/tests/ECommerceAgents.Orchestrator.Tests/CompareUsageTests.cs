using ECommerceAgents.Orchestrator.Routes;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Orchestration;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using Microsoft.Agents.AI;
using Microsoft.AspNetCore.Routing;
using Microsoft.Extensions.AI;
using Microsoft.Extensions.DependencyInjection;
using System.Net.Http.Json;
using System.Text.Json;
using Xunit;

namespace ECommerceAgents.Orchestrator.Tests;

/// <summary>
/// E-2c: <c>POST /api/orchestration/compare</c> reports tokens and estimated cost per mode.
/// A mode that makes no LLM call reports an explicit zero; one that reports nothing gets null.
/// </summary>
[Collection(nameof(LocalPostgresCollection))]
public sealed class CompareUsageTests
{
    private readonly PostgresFixture _pg;

    public CompareUsageTests(PostgresFixture pg) => _pg = pg;

    private sealed class WorkflowLikeMode : IOrchestrationMode
    {
        public string Name => "workflow:test";
        public string Label => "Workflow";
        public string Description => "no llm";
        public ModeCapabilities Capabilities => new();
        public string? GraphMermaid() => null;

        public Task<ModeRunResult> RunAsync(string message, RunContext ctx, CancellationToken ct = default) =>
            Task.FromResult(new ModeRunResult("done", ["stock"], 1, NoLlmCalls: true));
    }

    private sealed class SilentMode : IOrchestrationMode
    {
        public string Name => "silent";
        public string Label => "Silent";
        public string Description => "reports nothing";
        public ModeCapabilities Capabilities => new();
        public string? GraphMermaid() => null;

        public Task<ModeRunResult> RunAsync(string message, RunContext ctx, CancellationToken ct = default) =>
            Task.FromResult(new ModeRunResult("done", ["x"], 0));
    }

    [Fact]
    public async Task Compare_ReportsTokensAndCost_ZeroForWorkflow_NullWhenNotCaptured()
    {
        await using var pool = new DatabasePool(new AgentSettings { DatabaseUrl = _pg.ConnectionString });
        var chat = new FakeChatClient { Usage = new UsageDetails { InputTokenCount = 1000, OutputTokenCount = 100 } };
        chat.EnqueueResponse("A great deal.");

        var server = OrchestratorTestHost.Create(
            pool,
            r => r.MapOrchestrationRoutes(),
            configureServices: services =>
            {
                services.AddSingleton<IChatClient>(chat);
                services.AddSingleton<AIAgent>(sp =>
                    sp.GetRequiredService<IChatClient>().AsAIAgent(instructions: "test", name: "orchestrator"));
                services.AddSingleton<IOrchestrationMode, WorkflowLikeMode>();
                services.AddSingleton<IOrchestrationMode, SilentMode>();
            });
        using var client = server.CreateClient();
        client.DefaultRequestHeaders.Add("X-Test-Email", "compare@example.com");

        var res = await client.PostAsJsonAsync(
            "/api/orchestration/compare",
            new { message = "worth it?", modes = new[] { "tool", "workflow:test", "silent" } });
        res.EnsureSuccessStatusCode();
        var results = (await res.Content.ReadFromJsonAsync<JsonElement>()).GetProperty("results");

        var tool = results[0];
        tool.GetProperty("tokens_in").GetInt64().Should().Be(1000);
        tool.GetProperty("tokens_out").GetInt64().Should().Be(100);
        tool.GetProperty("est_cost_usd").GetDouble().Should().BeGreaterThan(0);
        tool.GetProperty("text").GetString().Should().NotContain("token");

        var workflow = results[1];
        workflow.GetProperty("tokens_in").GetInt64().Should().Be(0);
        workflow.GetProperty("est_cost_usd").GetDouble().Should().Be(0);

        var silent = results[2];
        silent.GetProperty("tokens_in").ValueKind.Should().Be(JsonValueKind.Null);
        silent.GetProperty("est_cost_usd").ValueKind.Should().Be(JsonValueKind.Null);
    }
}
