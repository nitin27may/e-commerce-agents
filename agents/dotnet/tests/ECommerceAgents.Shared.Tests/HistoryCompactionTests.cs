using Dapper;
using ECommerceAgents.Shared.A2A;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using Microsoft.Extensions.Configuration;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>B-15: mirrors Python's tests/test_history_compaction.py. Whole turns, and off is the same list.</summary>
public sealed class HistoryCompactionTests
{
    private static List<HistoryEntry> Turns(int n)
    {
        var list = new List<HistoryEntry>();
        for (var i = 0; i < n; i++)
        {
            list.Add(new HistoryEntry("user", $"q{i}"));
            list.Add(new HistoryEntry("assistant", $"a{i}"));
        }

        return list;
    }

    [Fact]
    public void Off_ReturnsTheSameListObject()
    {
        var history = Turns(30);

        HistoryCompaction.Compact(history, 0).Should().BeSameAs(history);
        HistoryCompaction.Compact(history, -3).Should().BeSameAs(history);
    }

    [Fact]
    public void UnderTheWindow_IsUntouched()
    {
        var history = Turns(3);

        HistoryCompaction.Compact(history, 10).Should().BeSameAs(history);
    }

    [Fact]
    public void KeepsTheLastNTurnsWhole()
    {
        var kept = HistoryCompaction.Compact(Turns(30), 10);

        HistoryCompaction.Group(kept).Should().HaveCount(10);
        kept[0].Should().Be(new HistoryEntry("user", "q20"));
        kept[^1].Should().Be(new HistoryEntry("assistant", "a29"));
    }

    [Fact]
    public void ToolCallAndResult_AreDroppedTogether()
    {
        var history = new List<HistoryEntry>
        {
            new("user", "old"),
            new("assistant", "calling"),
            new("tool", "result"),
            new("assistant", "done"),
            new("user", "new"),
            new("assistant", "ok"),
        };

        var one = HistoryCompaction.Compact(history, 1);
        one.Should().Equal(history.Skip(4));
        one.Should().NotContain(m => m.Role == "tool");

        // Keeping two turns keeps the call and its result as a pair.
        var two = HistoryCompaction.Compact(history, 2);
        two.Count(m => m.Role == "tool").Should().Be(1);
        two.Should().Equal(history);
    }

    [Fact]
    public void LeadingOrphanGroup_IsDroppedFirst()
    {
        var history = new List<HistoryEntry> { new("assistant", "orphan") };
        history.AddRange(Turns(2));

        HistoryCompaction.Compact(history, 2).Should().Equal(Turns(2));
    }

    [Fact]
    public void Empty_IsReturnedAsIs()
    {
        var empty = new List<HistoryEntry>();

        HistoryCompaction.Compact(empty, 5).Should().BeSameAs(empty);
    }

    [Fact]
    public void Setting_DefaultsToOff_AndReadsTheEnvVar()
    {
        AgentSettingsLoader.Load(new ConfigurationBuilder().Build()).HistoryCompactionKeepLastGroups.Should().Be(0);

        var config = new ConfigurationBuilder()
            .AddInMemoryCollection(new Dictionary<string, string?> { ["HISTORY_COMPACTION_KEEP_LAST_GROUPS"] = "10" })
            .Build();
        AgentSettingsLoader.Load(config).HistoryCompactionKeepLastGroups.Should().Be(10);
    }
}

/// <summary>The rehydrated 60-message conversation reaching the model trimmed (10 turns) or untouched (50 rows).</summary>
[Collection(nameof(LocalPostgresCollection))]
public sealed class HistoryCompactionRehydrationTests(PostgresFixture pg) : IAsyncLifetime
{
    private DatabasePool _pool = null!;

    public async Task InitializeAsync()
    {
        _pool = new DatabasePool(new AgentSettings { DatabaseUrl = pg.ConnectionString });
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync("TRUNCATE messages, conversations, users RESTART IDENTITY CASCADE");
    }

    public async Task DisposeAsync() => await _pool.DisposeAsync();

    [Theory]
    [InlineData(10, 20, "m40")]
    [InlineData(0, 50, "m10")]
    public async Task RehydrateAsync_AppliesTheConfiguredWindow(int keep, int expectedCount, string expectedFirst)
    {
        string conversationId;
        await using (var conn = await _pool.OpenAsync())
        {
            var userId = await conn.ExecuteScalarAsync<Guid>(
                "INSERT INTO users (email, password_hash, name, role) VALUES ('compact@example.com', 'x', 'C', 'customer') RETURNING id");
            var cid = await conn.ExecuteScalarAsync<Guid>(
                "INSERT INTO conversations (user_id, title) VALUES (@uid, 't') RETURNING id", new { uid = userId });
            conversationId = cid.ToString();
            for (var i = 0; i < 60; i++)
            {
                await conn.ExecuteAsync(
                    @"INSERT INTO messages (conversation_id, role, content, created_at)
                      VALUES (@cid, @role, @content, NOW() - (@ago || ' seconds')::interval)",
                    new { cid, role = i % 2 == 0 ? "user" : "assistant", content = $"m{i}", ago = 60 - i });
            }
        }

        using var _ = RequestContext.Scope("compact@example.com", "customer", "", []);

        var history = await HistoryRehydrator.RehydrateAsync(_pool, conversationId, keep);

        history.Should().NotBeNull();
        history!.Should().HaveCount(expectedCount);
        history[0].Role.Should().Be("user", "a kept window starts on a user turn");
        history[0].Content.Should().Be(expectedFirst);
        history[^1].Content.Should().Be("m59");
    }
}
