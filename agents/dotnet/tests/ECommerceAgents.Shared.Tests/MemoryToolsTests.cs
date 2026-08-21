using Dapper;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Tools;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using System.Text.Json;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// Long-term memory writes on .NET (#19).
/// </summary>
/// <remarks>
/// .NET could read memories but not write one, so the Profile page's "AI Memory" card
/// told users to chat in order to build a profile while chatting could never add
/// anything on this backend. Every memory it displayed had been written by the Python
/// stack against the shared database.
/// </remarks>
[Collection(nameof(LocalPostgresCollection))]
public sealed class MemoryToolsTests : IAsyncLifetime
{
    private readonly PostgresFixture _pg;
    private DatabasePool _pool = null!;
    private MemoryTools _tools = null!;
    private const string Email = "memory@example.com";
    private const string Other = "someone.else@example.com";

    public MemoryToolsTests(PostgresFixture pg) => _pg = pg;

    public async Task InitializeAsync()
    {
        _pool = new DatabasePool(new AgentSettings { DatabaseUrl = _pg.ConnectionString });
        _tools = new MemoryTools(_pool);

        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync("TRUNCATE agent_memories, users RESTART IDENTITY CASCADE");
        foreach (var email in new[] { Email, Other })
        {
            await conn.ExecuteAsync(
                "INSERT INTO users (email, password_hash, name, role) VALUES (@email, 'x', 'T', 'customer')",
                new { email });
        }
    }

    public async Task DisposeAsync() => await _pool.DisposeAsync();

    [Fact]
    public async Task AStoredMemoryCanBeRecalled()
    {
        using var scope = RequestContext.Scope(Email, "customer", sessionId: "");

        var stored = Json(await _tools.StoreMemory("preference", "Prefers noise-cancelling headphones", 8));
        stored.GetProperty("stored").GetBoolean().Should().BeTrue();

        var recalled = Json(await _tools.RecallMemories());
        recalled.GetArrayLength().Should().Be(1);
        recalled[0].GetProperty("content").GetString().Should().Be("Prefers noise-cancelling headphones");
        recalled[0].GetProperty("importance").GetInt32().Should().Be(8);
    }

    [Fact]
    public async Task MemoriesAreScopedToTheCaller_AndNotLeakedAcrossUsers()
    {
        using (var mine = RequestContext.Scope(Email, "customer", sessionId: ""))
        {
            await _tools.StoreMemory("preference", "mine", 5);
        }

        using var theirs = RequestContext.Scope(Other, "customer", sessionId: "");
        Json(await _tools.RecallMemories()).GetArrayLength().Should().Be(0,
            "identity comes from the request, so one user's profile cannot surface in another's");
    }

    [Fact]
    public async Task AnUnknownCategoryIsRefused_NotFiledSomewhereNothingReads()
    {
        using var scope = RequestContext.Scope(Email, "customer", sessionId: "");

        var result = Json(await _tools.StoreMemory("random-nonsense", "something", 5));

        result.GetProperty("error").GetString().Should().Contain("category must be one of");
        Json(await _tools.RecallMemories()).GetArrayLength().Should().Be(0);
    }

    [Fact]
    public async Task ImportanceIsClampedRatherThanRejected()
    {
        using var scope = RequestContext.Scope(Email, "customer", sessionId: "");

        await _tools.StoreMemory("behavior", "way too important", 99);
        await _tools.StoreMemory("behavior", "not important at all", -4);

        var recalled = Json(await _tools.RecallMemories());
        recalled.EnumerateArray().Select(m => m.GetProperty("importance").GetInt32())
            .Should().BeEquivalentTo([10, 1]);
    }

    [Fact]
    public async Task RecallCanFilterByCategory()
    {
        using var scope = RequestContext.Scope(Email, "customer", sessionId: "");
        await _tools.StoreMemory("preference", "likes teal", 5);
        await _tools.StoreMemory("feedback", "found checkout slow", 5);

        var prefs = Json(await _tools.RecallMemories(category: "preference"));

        prefs.GetArrayLength().Should().Be(1);
        prefs[0].GetProperty("content").GetString().Should().Be("likes teal");
    }

    [Fact]
    public async Task WithoutAnAuthenticatedUser_NothingIsStored()
    {
        // No RequestContext scope at all — the anonymous storefront case.
        var result = Json(await _tools.StoreMemory("preference", "should not persist", 5));

        result.GetProperty("error").GetString().Should().Contain("No authenticated user");
    }

    private static JsonElement Json(object value) =>
        value is JsonElement e ? e : JsonSerializer.SerializeToElement(value);
}
