using Dapper;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Orchestration;
using ECommerceAgents.Shared.Telemetry;
using ECommerceAgents.Orchestrator.Routes;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;
using Microsoft.Extensions.DependencyInjection;
using System.Net.Http.Json;
using Xunit;

namespace ECommerceAgents.Orchestrator.Tests;

/// <summary>
/// A workflow that stops on a human has to leave a durable trace, or the approval is
/// unreachable however well the pause itself works.
/// </summary>
/// <remarks>
/// Until this landed, .NET wrote no <c>hitl_requests</c> row at all — the pending badge
/// visible on <c>/runs</c> against the .NET stack came from three demo rows in
/// <c>scripts/seed.py</c>, not from anything the app did. The mode paused, the pause was
/// reported as an SSE frame, and then it was gone.
/// </remarks>
[Collection(nameof(LocalPostgresCollection))]
public sealed class HitlPausePersistenceTests : IAsyncLifetime
{
    private readonly PostgresFixture _pg;
    private DatabasePool _pool = null!;
    private const string Email = "pause@example.com";

    public HitlPausePersistenceTests(PostgresFixture pg) => _pg = pg;

    public async Task InitializeAsync()
    {
        _pool = new DatabasePool(new AgentSettings { DatabaseUrl = _pg.ConnectionString });
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            @"TRUNCATE hitl_requests, workflow_checkpoints, agent_execution_steps, usage_logs,
                       messages, conversations, users RESTART IDENTITY CASCADE");
        await conn.ExecuteAsync(
            "INSERT INTO users (email, password_hash, name, role) VALUES (@Email, 'x', 'Pause Tester', 'customer')",
            new { Email });
    }

    public async Task DisposeAsync() => await _pool.DisposeAsync();

    [Fact]
    public async Task APausedWorkflowRun_LeavesAPendingApprovalLinkedToItsCheckpoint()
    {
        using var client = ClientFor(new PausingMode(_pool));

        var response = await client.PostAsJsonAsync("/api/chat", new { message = "return it", mode = PausingMode.ModeName });
        response.EnsureSuccessStatusCode();

        await using var conn = await _pool.OpenAsync();
        var row = await conn.QueryFirstOrDefaultAsync(
            @"SELECT h.request_id, h.status, h.kind, h.user_email, h.checkpoint_id, h.workflow_run_id
              FROM hitl_requests h");

        ((object?)row).Should().NotBeNull("a pause with no row is an approval nobody can ever give");
        ((string)row!.request_id).Should().Be(PausingMode.RequestId);
        ((string)row.status).Should().Be("pending");
        ((string)row.kind).Should().Be("return_approval");
        ((string)row.user_email).Should().Be(Email);
        ((Guid?)row.checkpoint_id).Should().NotBeNull(
            "without the checkpoint the row exists but resume has nothing to restore from");

        // The same back-link is what stops GET /api/runs/{id}/checkpoints answering empty.
        var linked = await conn.ExecuteScalarAsync<Guid?>(
            "SELECT usage_log_id FROM workflow_checkpoints WHERE checkpoint_id = @cid",
            new { cid = (Guid?)row.checkpoint_id });
        linked.Should().Be((Guid)row.workflow_run_id);
    }

    [Fact]
    public async Task ARunThatDidNotPause_LeavesNoApprovalRow()
    {
        using var client = ClientFor(new PausingMode(_pool, pauses: false));

        var response = await client.PostAsJsonAsync("/api/chat", new { message = "hi", mode = PausingMode.ModeName });
        response.EnsureSuccessStatusCode();

        await using var conn = await _pool.OpenAsync();
        (await conn.ExecuteScalarAsync<int>("SELECT COUNT(*) FROM hitl_requests")).Should().Be(0);
    }

    private HttpClient ClientFor(IOrchestrationMode mode)
    {
        var server = OrchestratorTestHost.Create(
            _pool,
            r => r.MapChatRoutes(),
            configureServices: services =>
            {
                services.AddSingleton<IChatClient>(new FakeChatClient().EnqueueResponse("unused"));
                services.AddSingleton<AIAgent>(sp =>
                    sp.GetRequiredService<IChatClient>().AsAIAgent(instructions: "t", name: "orchestrator"));
                services.AddSingleton<UsageRecorder>();
                services.AddSingleton(mode);
            });
        var client = server.CreateClient();
        client.DefaultRequestHeaders.Add("X-Test-Email", Email);
        return client;
    }

    /// <summary>
    /// Stands in for ReturnReplaceMode. A real workflow run would need the whole tool
    /// chain and an LLM; what is under test is whether ChatRoutes persists a pause a mode
    /// reports, so the mode only has to report one.
    /// </summary>
    private sealed class PausingMode(DatabasePool pool, bool pauses = true) : IOrchestrationMode
    {
        public const string ModeName = "workflow:return-replace";
        public const string RequestId = "req-abc-123";

        public string Name => ModeName;
        public string Label => "Return & Replace";
        public string Description => "test";
        public ModeCapabilities Capabilities => new(Streams: false, SupportsHitl: true, IsGraph: true);
        public string? GraphMermaid() => null;

        public async Task<ModeRunResult> RunAsync(string message, RunContext ctx, CancellationToken ct = default)
        {
            if (!pauses)
            {
                return new ModeRunResult("done", ["orchestrator"], 1);
            }

            // A real run writes its checkpoints through the store during execution, so
            // the row exists before ChatRoutes tries to back-link it.
            var checkpointId = Guid.NewGuid();
            await using var conn = await pool.OpenAsync();
            await conn.ExecuteAsync(
                @"INSERT INTO workflow_checkpoints (checkpoint_id, workflow_name, payload, session_id)
                  VALUES (@checkpointId, 'return-replace', '{}'::jsonb, @sessionId)",
                new { checkpointId, sessionId = "session-1" });

            return new ModeRunResult(
                "Waiting on approval.", ["orchestrator"], 1,
                PendingApproval: true, RequestId: RequestId,
                LatestCheckpointId: checkpointId.ToString(), SessionId: "session-1");
        }
    }
}
