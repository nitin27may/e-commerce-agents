using Dapper;
using ECommerceAgents.Shared.Approvals;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Policy;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using Xunit;
using S = ECommerceAgents.Shared.Approvals.ApprovalStatus;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// D-5n — the cross-stack contract. The Python worker (<c>agents/python/worker/</c>) drains the outbox timers for approvals
/// created by EITHER stack, so what .NET writes has to be what Python's handlers read.
/// </summary>
/// <remarks>
/// The rows .NET writes are dumped straight from Postgres and compared, shape for shape, with
/// <c>agents/python/tests/data/dotnet_approval_rows.json</c>. That same file is what
/// <c>agents/python/tests/test_approval_dotnet_rows.py</c> loads into a Python database and feeds to the real worker handlers, so a change
/// to what .NET writes cannot pass here without the fixture being regenerated, and the fixture cannot be regenerated without Python
/// proving it still understands it. Regenerate with
/// <c>APPROVAL_DUMP_PATH=&lt;repo&gt;/agents/python/tests/data/dotnet_approval_rows.json dotnet test --filter ApprovalCrossStackTests</c>.
/// </remarks>
[Collection(nameof(LocalPostgresCollection))]
public sealed class ApprovalCrossStackTests : IAsyncLifetime
{
    private readonly PostgresFixture _pg;
    private DatabasePool _pool = null!;
    private AgentSettings _settings = null!;

    public ApprovalCrossStackTests(PostgresFixture pg) => _pg = pg;

    public async Task InitializeAsync()
    {
        _settings = new AgentSettings { DatabaseUrl = _pg.ConnectionString };
        _pool = new DatabasePool(_settings);
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync("TRUNCATE approval_events, approvals, outbox RESTART IDENTITY CASCADE");
    }

    public async Task DisposeAsync() => await _pool.DisposeAsync();

    private static string FixturePath()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !File.Exists(Path.Combine(dir.FullName, "docker", "postgres", "init.sql"))) dir = dir.Parent;
        dir.Should().NotBeNull("the repo root holds docker/postgres/init.sql");
        return Path.Combine(dir!.FullName, "agents", "python", "tests", "data", "dotnet_approval_rows.json");
    }

    /// <summary>Everything the .NET stack writes when a gated tool call opens its approval: the row, its audit events, its timers.</summary>
    private async Task<JsonObject> DumpAsync()
    {
        var repo = new ApprovalRepository(_pool, _settings, ApprovalPolicyLocator.Current);
        var intake = new ApprovalIntake(_pool, repo, ApprovalPolicyLocator.Current);
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            "INSERT INTO users (email, password_hash, name, role) VALUES ('xstack@example.com', 'x', 'X', 'customer') ON CONFLICT DO NOTHING");
        var requestId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO tool_approval_requests (user_email, agent_name, tool_name, tool_input)
              VALUES ('xstack@example.com', 'order-management', 'cancel_order', '{""order_id"": ""00000000-0000-0000-0000-000000000001""}'::jsonb)
              RETURNING id");
        using var input = JsonDocument.Parse("""{"order_id": "00000000-0000-0000-0000-000000000001"}""");
        var approval = await intake.OpenToolApprovalAsync(requestId, "xstack@example.com", null, "CancelOrder", input.RootElement.Clone());
        approval.Status.Should().Be(S.PendingReview);

        JsonNode Rows(string json) => JsonNode.Parse(json)!;
        return new JsonObject
        {
            ["approval"] = Rows(await conn.ExecuteScalarAsync<string>(
                "SELECT to_jsonb(a)::text FROM approvals a WHERE id = @id", new { id = approval.Id }) ?? "null"),
            ["events"] = Rows(await conn.ExecuteScalarAsync<string>(
                "SELECT coalesce(jsonb_agg(to_jsonb(e) ORDER BY e.id), '[]'::jsonb)::text FROM approval_events e WHERE approval_id = @id",
                new { id = approval.Id }) ?? "null"),
            ["outbox"] = Rows(await conn.ExecuteScalarAsync<string>(
                "SELECT coalesce(jsonb_agg(to_jsonb(o) ORDER BY o.id), '[]'::jsonb)::text FROM outbox o WHERE aggregate_id = @id",
                new { id = approval.Id }) ?? "null"),
        };
    }

    private static readonly Regex Uuid = new("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", RegexOptions.Compiled);
    private static readonly Regex Instant = new(@"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(\+00:00|Z)?", RegexOptions.Compiled);
    private static readonly Regex EpochMs = new(@":\d{12,13}$", RegexOptions.Compiled);

    /// <summary>Ids, instants and the epoch stamp in a dedupe key vary run to run; everything else must match exactly.</summary>
    private static JsonNode? Normalise(JsonNode? node) => node switch
    {
        JsonObject o => new JsonObject(o.Select(p => KeyValuePair.Create(p.Key, Normalise(p.Value)))),
        JsonArray a => new JsonArray(a.Select(Normalise).ToArray()),
        JsonValue v when v.TryGetValue<string>(out var s) =>
            JsonValue.Create(EpochMs.Replace(Instant.Replace(Uuid.Replace(s, "<uuid>"), "<instant>"), ":<ms>")),
        _ => node?.DeepClone(),
    };

    [Fact]
    public async Task WhatDotnetWritesForAnApproval_MatchesTheFixturePythonsWorkerIsTestedAgainst()
    {
        var dump = await DumpAsync();

        // Regeneration path: only when asked for explicitly.
        if (Environment.GetEnvironmentVariable("APPROVAL_DUMP_PATH") is { Length: > 0 } target)
        {
            Directory.CreateDirectory(Path.GetDirectoryName(target)!);
            await File.WriteAllTextAsync(target, dump.ToJsonString(new JsonSerializerOptions { WriteIndented = true }) + "\n");
        }

        var committed = JsonNode.Parse(await File.ReadAllTextAsync(FixturePath()))!;
        Normalise(dump)!.ToJsonString().Should().Be(
            Normalise(committed)!.ToJsonString(),
            "the Python worker test loads this fixture; regenerate it (see the class remarks) if the .NET write path changed on purpose");
    }

    [Fact]
    public async Task TheDumpHasTheTwoTimerRowsTheWorkerExpects()
    {
        var dump = await DumpAsync();
        var outbox = dump["outbox"]!.AsArray();
        outbox.Select(r => r!["topic"]!.GetValue<string>()).Should().BeEquivalentTo("approval.reminder_due", "approval.sla_breached");
        var due = dump["approval"]!["due_at"]!.GetValue<string>();
        foreach (var row in outbox)
        {
            DateTimeOffset.Parse(row!["payload"]!["due_at"]!.GetValue<string>()).Should().Be(DateTimeOffset.Parse(due));
        }
    }
}
