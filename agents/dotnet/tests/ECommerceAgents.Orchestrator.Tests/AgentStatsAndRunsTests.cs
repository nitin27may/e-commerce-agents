using Dapper;
using ECommerceAgents.Orchestrator.Routes;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Xunit;

namespace ECommerceAgents.Orchestrator.Tests;

/// <summary>
/// <c>GET /api/agents/stats</c> (<see cref="AgentStatsRoutes"/>) and
/// <c>GET /api/runs</c> (<see cref="RunsRoutes"/>) — both previously missing
/// entirely from the .NET orchestrator. Mirrors Python's <c>get_agent_stats</c>
/// and <c>list_runs</c> (<c>routes.py:1095-1117</c> / <c>:1406-1491</c>).
/// </summary>
[Collection(nameof(LocalPostgresCollection))]
public sealed class AgentStatsAndRunsTests : IAsyncLifetime
{
    private readonly PostgresFixture _pg;
    private DatabasePool _pool = null!;
    private const string AliceEmail = "alice-stats@example.com";
    private const string BobEmail = "bob-stats@example.com";
    private Guid _aliceId;
    private Guid _bobId;

    public AgentStatsAndRunsTests(PostgresFixture pg) => _pg = pg;

    public async Task InitializeAsync()
    {
        var settings = new AgentSettings { DatabaseUrl = _pg.ConnectionString };
        _pool = new DatabasePool(settings);

        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            @"TRUNCATE order_status_history, order_items, returns, orders,
                       messages, conversations, warehouse_inventory,
                       warehouses, agent_execution_steps, usage_logs,
                       reviews, products, users
              RESTART IDENTITY CASCADE"
        );
        _aliceId = await conn.ExecuteScalarAsync<Guid>(
            "INSERT INTO users (email, password_hash, name, role) VALUES (@e, 'x', 'Alice', 'customer') RETURNING id",
            new { e = AliceEmail }
        );
        _bobId = await conn.ExecuteScalarAsync<Guid>(
            "INSERT INTO users (email, password_hash, name, role) VALUES (@e, 'x', 'Bob', 'customer') RETURNING id",
            new { e = BobEmail }
        );
    }

    public async Task DisposeAsync() => await _pool.DisposeAsync();

    private HttpClient ClientFor(
        Action<Microsoft.AspNetCore.Routing.IEndpointRouteBuilder> map,
        string? email = AliceEmail,
        string role = "customer"
    )
    {
        var server = OrchestratorTestHost.Create(_pool, map);
        var client = server.CreateClient();
        if (email is not null)
        {
            client.DefaultRequestHeaders.Add("X-Test-Email", email);
            client.DefaultRequestHeaders.Add("X-Test-Role", role);
        }
        return client;
    }

    // ─────────────────────── agents/stats ────────────────────

    [Fact]
    public async Task AgentStats_AggregatesByAgentNameOver30Days()
    {
        await using (var conn = await _pool.OpenAsync())
        {
            await conn.ExecuteAsync(
                @"INSERT INTO usage_logs (user_id, agent_name, tokens_in, tokens_out, duration_ms, status)
                  VALUES
                    (@u, 'orchestrator', 10, 20, 100, 'success'),
                    (@u, 'orchestrator', 10, 20, 200, 'success'),
                    (@u, 'product-discovery', 5, 5, 50, 'success')",
                new { u = _aliceId }
            );
        }

        using var client = ClientFor(r => r.MapAgentStatsRoutes());
        var response = await client.GetAsync("/api/agents/stats");
        response.EnsureSuccessStatusCode();
        var payload = await response.Content.ReadFromJsonAsync<JsonElement>();

        payload.GetArrayLength().Should().Be(2);
        var orchestratorRow = payload.EnumerateArray()
            .First(e => e.GetProperty("agent_name").GetString() == "orchestrator");
        orchestratorRow.GetProperty("request_count").GetInt64().Should().Be(2);
        orchestratorRow.GetProperty("avg_duration_ms").GetInt32().Should().Be(150);
        orchestratorRow.GetProperty("total_tokens").GetInt64().Should().Be(60);
    }

    [Fact]
    public async Task AgentStats_RejectsAnonymous()
    {
        using var client = ClientFor(r => r.MapAgentStatsRoutes(), email: null);
        var response = await client.GetAsync("/api/agents/stats");
        response.StatusCode.Should().Be(HttpStatusCode.Unauthorized);
    }

    // ─────────────────────── runs ─────────────────────────────

    private async Task SeedRunsForBothUsersAsync()
    {
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            @"INSERT INTO usage_logs (user_id, agent_name, input_summary, duration_ms, status)
              VALUES
                (@a, 'orchestrator', 'alice turn 1', 100, 'success'),
                (@a, 'orchestrator', 'alice turn 2', 100, 'success'),
                (@b, 'orchestrator', 'bob turn 1', 100, 'success')",
            new { a = _aliceId, b = _bobId }
        );
    }

    [Fact]
    public async Task Runs_NonAdmin_SeesOnlyOwnRuns()
    {
        await SeedRunsForBothUsersAsync();
        using var client = ClientFor(r => r.MapRunsRoutes(), AliceEmail, role: "customer");

        var response = await client.GetAsync("/api/runs");
        response.EnsureSuccessStatusCode();
        var payload = await response.Content.ReadFromJsonAsync<JsonElement>();

        payload.GetProperty("total").GetInt64().Should().Be(2);
        payload.GetProperty("entries").EnumerateArray().Should()
            .OnlyContain(e => e.GetProperty("user_email").GetString() == AliceEmail);
    }

    [Fact]
    public async Task Runs_Support_SeesOnlyOwnRuns_ReviewingHappensInTheApprovalsInbox()
    {
        // Staff may decide an approval, but /api/runs stays scoped: only admins read other users' runs. Support
        // reviews other people's requests from /api/approvals, not from this list (docs/checkpoints-and-resume.md).
        await SeedRunsForBothUsersAsync();
        using var client = ClientFor(r => r.MapRunsRoutes(), "support-stats@example.com", role: "support");

        var payload = await client.GetFromJsonAsync<JsonElement>("/api/runs");

        payload.GetProperty("total").GetInt64().Should().Be(0);
    }

    [Fact]
    public async Task Runs_Admin_SeesEveryUsersRuns()
    {
        await SeedRunsForBothUsersAsync();
        using var client = ClientFor(r => r.MapRunsRoutes(), "admin-stats@example.com", role: "admin");

        var response = await client.GetAsync("/api/runs");
        response.EnsureSuccessStatusCode();
        var payload = await response.Content.ReadFromJsonAsync<JsonElement>();

        payload.GetProperty("total").GetInt64().Should().Be(3);
    }

    [Fact]
    public async Task Runs_EntryShape_OmitsErrorMessageUnlikeAdminAudit()
    {
        // Matches Python: /api/runs' entry dict doesn't include error_message
        // (unlike /api/admin/audit's), by design — not an oversight.
        await SeedRunsForBothUsersAsync();
        using var client = ClientFor(r => r.MapRunsRoutes(), AliceEmail);

        var response = await client.GetAsync("/api/runs");
        var payload = await response.Content.ReadFromJsonAsync<JsonElement>();
        var entry = payload.GetProperty("entries")[0];

        entry.TryGetProperty("error_message", out _).Should().BeFalse();
        entry.TryGetProperty("steps", out _).Should().BeTrue();
    }

    [Fact]
    public async Task Runs_RejectsAnonymous()
    {
        using var client = ClientFor(r => r.MapRunsRoutes(), email: null);
        var response = await client.GetAsync("/api/runs");
        response.StatusCode.Should().Be(HttpStatusCode.Unauthorized);
    }
}
