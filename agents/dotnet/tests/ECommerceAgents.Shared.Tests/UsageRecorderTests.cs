using Dapper;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Telemetry;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

[Collection(nameof(LocalPostgresCollection))]
public sealed class UsageRecorderTests : IAsyncLifetime
{
    private readonly PostgresFixture _pg;
    private DatabasePool _pool = null!;
    private Guid _userId;

    public UsageRecorderTests(PostgresFixture pg) => _pg = pg;

    public async Task InitializeAsync()
    {
        _pool = new DatabasePool(new AgentSettings { DatabaseUrl = _pg.ConnectionString });

        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            @"TRUNCATE agent_execution_steps, usage_logs, users RESTART IDENTITY CASCADE"
        );
        _userId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO users (email, password_hash, name, role)
              VALUES ('u@example.com', 'x', 'U', 'customer') RETURNING id"
        );
    }

    public async Task DisposeAsync()
    {
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            @"TRUNCATE agent_execution_steps, usage_logs, users RESTART IDENTITY CASCADE"
        );
        await _pool.DisposeAsync();
    }

    [Fact]
    public async Task LogAgentUsage_PersistsRowAndReturnsId()
    {
        var recorder = new UsageRecorder(_pool);
        var id = await recorder.LogAgentUsageAsync(
            userId: _userId,
            agentName: "orchestrator",
            inputSummary: "hello",
            tokensIn: 100,
            tokensOut: 200,
            toolCallsCount: 1,
            durationMs: 750
        );

        id.Should().NotBeNull();

        await using var conn = await _pool.OpenAsync();
        var row = await conn.QueryFirstAsync(
            "SELECT agent_name, tokens_in, tokens_out, tool_calls_count, duration_ms, status FROM usage_logs WHERE id = @id",
            new { id }
        );
        string agent = row.agent_name;
        int tIn = Convert.ToInt32(row.tokens_in);
        int tOut = Convert.ToInt32(row.tokens_out);
        int calls = Convert.ToInt32(row.tool_calls_count);
        int dur = Convert.ToInt32(row.duration_ms);
        string stat = row.status;
        agent.Should().Be("orchestrator");
        tIn.Should().Be(100);
        tOut.Should().Be(200);
        calls.Should().Be(1);
        dur.Should().Be(750);
        stat.Should().Be("success");
    }

    [Fact]
    public async Task LogAgentUsage_TruncatesInputSummaryAt500Chars()
    {
        var recorder = new UsageRecorder(_pool);
        var huge = new string('x', 1200);
        var id = await recorder.LogAgentUsageAsync(_userId, "orch", inputSummary: huge);
        id.Should().NotBeNull();

        await using var conn = await _pool.OpenAsync();
        var summary = await conn.ExecuteScalarAsync<string>(
            "SELECT input_summary FROM usage_logs WHERE id = @id", new { id }
        );
        summary.Should().HaveLength(500);
    }

    [Fact]
    public async Task LogExecutionStep_PersistsToolInputAndOutput()
    {
        var recorder = new UsageRecorder(_pool);
        var usageId = await recorder.LogAgentUsageAsync(_userId, "orchestrator");
        usageId.Should().NotBeNull();

        await recorder.LogExecutionStepAsync(
            usageLogId: usageId!.Value,
            stepIndex: 0,
            toolName: "check_stock",
            toolInput: new { product_id = "p-1" },
            toolOutput: new { in_stock = true, total_quantity = 42 },
            durationMs: 55
        );

        await using var conn = await _pool.OpenAsync();
        var row = await conn.QueryFirstAsync(
            @"SELECT step_index, tool_name, tool_input, tool_output, duration_ms
              FROM agent_execution_steps WHERE usage_log_id = @id",
            new { id = usageId }
        );
        int idx = Convert.ToInt32(row.step_index);
        string toolName = row.tool_name;
        int dur = Convert.ToInt32(row.duration_ms);
        string input = row.tool_input?.ToString() ?? "";
        string output = row.tool_output?.ToString() ?? "";
        idx.Should().Be(0);
        toolName.Should().Be("check_stock");
        dur.Should().Be(55);
        input.Should().Contain("p-1");
        output.Should().Contain("42");
    }

    [Fact]
    public async Task LogExecutionStep_HandlesNullPayloads()
    {
        var recorder = new UsageRecorder(_pool);
        var usageId = await recorder.LogAgentUsageAsync(_userId, "orchestrator");
        await recorder.LogExecutionStepAsync(usageId!.Value, 0, "noop");

        await using var conn = await _pool.OpenAsync();
        var count = await conn.ExecuteScalarAsync<long>(
            "SELECT COUNT(*) FROM agent_execution_steps WHERE usage_log_id = @id",
            new { id = usageId }
        );
        count.Should().Be(1);
    }

    [Fact]
    public void UsageTimer_RecordsElapsedMillis()
    {
        int duration;
        using (var timer = new UsageTimer())
        {
            Thread.Sleep(50);
            duration = timer.DurationMs;
            // Still inside block — nothing recorded yet
            duration.Should().Be(0);
        }

        // After disposal DurationMs is populated; re-create and assert on a
        // fresh timer to avoid relying on a post-disposal read.
        using var measured = new UsageTimer();
        Thread.Sleep(40);
        measured.Dispose();
        measured.DurationMs.Should().BeGreaterThanOrEqualTo(30);
    }
}
