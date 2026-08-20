using Dapper;
using ECommerceAgents.Shared.A2A;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;
using Microsoft.Extensions.DependencyInjection;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// <see cref="AgentHost.RunAgentWithHistoryAsync"/> — the shared specialist
/// history-injection helper (plan Part B). Prefers <see cref="RequestContext.CurrentHistory"/>
/// (populated from a forwarded A2A payload); falls back to
/// <see cref="HistoryRehydrator.RehydrateAsync"/> via the session id when
/// nothing was forwarded — mirrors Python's <c>agent_host.py</c> exactly.
/// </summary>
[Collection(nameof(LocalPostgresCollection))]
public sealed class AgentHostHistoryTests : IAsyncLifetime
{
    private readonly PostgresFixture _pg;
    private DatabasePool _pool = null!;

    public AgentHostHistoryTests(PostgresFixture pg) => _pg = pg;

    public async Task InitializeAsync()
    {
        _pool = new DatabasePool(new AgentSettings { DatabaseUrl = _pg.ConnectionString });
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync("TRUNCATE messages, conversations, users RESTART IDENTITY CASCADE");
    }

    public async Task DisposeAsync() => await _pool.DisposeAsync();

    private IServiceProvider BuildServices(FakeChatClient chatClient)
    {
        var services = new ServiceCollection();
        services.AddSingleton(_pool);
        services.AddSingleton<AIAgent>(chatClient.AsAIAgent(instructions: "test instructions", name: "specialist"));
        return services.BuildServiceProvider();
    }

    [Fact]
    public async Task RunAgentWithHistoryAsync_PrefersForwardedHistoryOverRehydration()
    {
        var chatClient = new FakeChatClient().EnqueueResponse("reply");
        var services = BuildServices(chatClient);

        using var scope = RequestContext.Scope(
            "u@example.com",
            "customer",
            Guid.NewGuid().ToString(),
            new List<HistoryEntry> { new HistoryEntry("user", "forwarded turn") }
        );

        var result = await AgentHost.RunAgentWithHistoryAsync(services, "current message");

        result.Should().Be("reply");
        var messages = chatClient.ReceivedMessages.Single().ToList();
        messages.Select(m => (m.Role, m.Text)).Should().Equal(
            (ChatRole.User, "forwarded turn"),
            (ChatRole.User, "current message")
        );
    }

    [Fact]
    public async Task RunAgentWithHistoryAsync_FallsBackToRehydrationWhenNoHistoryForwarded()
    {
        var conversationId = await SeedConversationWithHistoryAsync();

        var chatClient = new FakeChatClient().EnqueueResponse("reply");
        var services = BuildServices(chatClient);

        // No history passed to Scope — CurrentHistory is empty, so the helper
        // must fall back to rehydrating from Postgres via the session id.
        using var scope = RequestContext.Scope("rehydrate@example.com", "customer", conversationId.ToString());

        var result = await AgentHost.RunAgentWithHistoryAsync(services, "new message");

        result.Should().Be("reply");
        var messages = chatClient.ReceivedMessages.Single().ToList();
        messages.Select(m => (m.Role, m.Text)).Should().Equal(
            (ChatRole.User, "earlier question"),
            (ChatRole.Assistant, "earlier answer"),
            (ChatRole.User, "new message")
        );
    }

    [Fact]
    public async Task RunAgentWithHistoryAsync_NoHistoryAndNoSessionId_SendsOnlyCurrentMessage()
    {
        var chatClient = new FakeChatClient().EnqueueResponse("reply");
        var services = BuildServices(chatClient);

        using var scope = RequestContext.Scope("u@example.com", "customer", "");

        var result = await AgentHost.RunAgentWithHistoryAsync(services, "solo message");

        result.Should().Be("reply");
        var messages = chatClient.ReceivedMessages.Single().ToList();
        messages.Should().ContainSingle();
        messages[0].Role.Should().Be(ChatRole.User);
        messages[0].Text.Should().Be("solo message");
    }

    // ─────────────────────── streaming (issue #14) ───────────

    [Fact]
    public async Task RunAgentWithHistoryStreamingAsync_YieldsTheChatClientsStreamedText()
    {
        var chatClient = new FakeChatClient().EnqueueResponse("streamed reply");
        var services = BuildServices(chatClient);
        using var scope = RequestContext.Scope("u@example.com", "customer", "");

        var chunks = new List<string>();
        await foreach (var chunk in AgentHost.RunAgentWithHistoryStreamingAsync(services, "hi"))
        {
            chunks.Add(chunk);
        }

        string.Concat(chunks).Should().Be("streamed reply");
    }

    [Fact]
    public async Task RunAgentWithHistoryStreamingAsync_BuildsTheSameMessageHistoryAsTheBlockingPath()
    {
        var conversationId = await SeedConversationWithHistoryAsync();
        var chatClient = new FakeChatClient().EnqueueResponse("reply");
        var services = BuildServices(chatClient);
        using var scope = RequestContext.Scope("rehydrate@example.com", "customer", conversationId.ToString());

        await foreach (var _ in AgentHost.RunAgentWithHistoryStreamingAsync(services, "new message"))
        {
            // drain
        }

        var messages = chatClient.ReceivedMessages.Single().ToList();
        messages.Select(m => (m.Role, m.Text)).Should().Equal(
            (ChatRole.User, "earlier question"),
            (ChatRole.Assistant, "earlier answer"),
            (ChatRole.User, "new message")
        );
    }

    private async Task<Guid> SeedConversationWithHistoryAsync()
    {
        await using var conn = await _pool.OpenAsync();
        var userId = await conn.ExecuteScalarAsync<Guid>(
            "INSERT INTO users (email, password_hash, name, role) VALUES ('rehydrate@example.com', 'x', 'R', 'customer') RETURNING id"
        );
        var conversationId = await conn.ExecuteScalarAsync<Guid>(
            "INSERT INTO conversations (user_id, title) VALUES (@uid, 'test') RETURNING id",
            new { uid = userId }
        );
        await conn.ExecuteAsync(
            @"INSERT INTO messages (conversation_id, role, content, agent_name, created_at) VALUES
              (@cid, 'user', 'earlier question', NULL, NOW() - INTERVAL '1 minutes'),
              (@cid, 'assistant', 'earlier answer', 'order-management', NOW())",
            new { cid = conversationId }
        );
        return conversationId;
    }
}
