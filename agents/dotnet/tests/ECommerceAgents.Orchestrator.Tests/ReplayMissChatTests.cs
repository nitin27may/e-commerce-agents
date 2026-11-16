using Dapper;
using ECommerceAgents.Orchestrator.Agent;
using ECommerceAgents.Orchestrator.Routes;
using ECommerceAgents.Shared.A2A;
using ECommerceAgents.Shared.Agents;
using ECommerceAgents.Shared.Auth;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Telemetry;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging.Abstractions;
using System.Net;
using System.Net.Http.Json;
using System.Runtime.CompilerServices;
using System.Text;
using System.Text.Json;
using Xunit;

namespace ECommerceAgents.Orchestrator.Tests;

/// <summary>
/// A replay miss (<c>LLM_PROVIDER=replay</c>, no recording for the request) ends a chat turn as a plain message, not a
/// 500 and not an apology, and takes the unanswered user message back out so later turns still hit their recordings.
/// The twin of Python's <c>tests/test_replay_miss.py</c> and <c>test_replay_miss_cleanup.py</c>; same wire shape to the
/// web client (<c>replay_miss: {message}</c> on <c>/api/chat</c>, an <c>event: replay_miss</c> frame on the stream).
/// The A2A 1.0 wire is covered against a real specialist in <c>A2AReplayMissTests</c> (Shared.Tests).
/// </summary>
[Collection(nameof(LocalPostgresCollection))]
public sealed class ReplayMissChatTests : IAsyncLifetime
{
    private const string Email = "replaymiss@example.com";
    private readonly PostgresFixture _pg;
    private DatabasePool _pool = null!;

    public ReplayMissChatTests(PostgresFixture pg) => _pg = pg;

    public async Task InitializeAsync()
    {
        _pool = new DatabasePool(new AgentSettings { DatabaseUrl = _pg.ConnectionString });
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            "TRUNCATE order_status_history, order_items, returns, orders, messages, conversations, users, usage_logs RESTART IDENTITY CASCADE");
        await conn.ExecuteAsync(
            "INSERT INTO users (email, password_hash, name, role) VALUES (@email, 'x', 'Miss Tester', 'customer')", new { email = Email });
    }

    public async Task DisposeAsync() => await _pool.DisposeAsync();

    // ─────────────────────── test doubles ───────────────────────

    /// <summary>The orchestrator's own model call has no recording.</summary>
    private sealed class MissingModel : IChatClient
    {
        public Task<ChatResponse> GetResponseAsync(IEnumerable<ChatMessage> messages, ChatOptions? options = null, CancellationToken ct = default) =>
            throw new ReplayChatClient.FixtureMissingException("abc123", "/fixtures");

        public async IAsyncEnumerable<ChatResponseUpdate> GetStreamingResponseAsync(
            IEnumerable<ChatMessage> messages, ChatOptions? options = null, [EnumeratorCancellation] CancellationToken ct = default)
        {
            await Task.CompletedTask;
            if (messages is not null)
            {
                throw new ReplayChatClient.FixtureMissingException("abc123", "/fixtures");
            }

            yield break;
        }

        public object? GetService(Type serviceType, object? serviceKey = null) => null;
        public void Dispose() { }
    }

    /// <summary>Asks for one specialist call, then answers: the second call is what a miss must never reach.</summary>
    private sealed class RoutingModel : IChatClient
    {
        public int Calls { get; private set; }

        public string LastToolResult { get; private set; } = "";

        private ChatMessage Reply()
        {
            Calls++;
            return Calls == 1
                ? new ChatMessage(ChatRole.Assistant, [new FunctionCallContent("call-1", "call_specialist_agent",
                    new Dictionary<string, object?> { ["agent_name"] = "product-discovery", ["message"] = "tents?" })])
                : new ChatMessage(ChatRole.Assistant, "the model answered the miss as if it were a result");
        }

        private ChatResponse Capture(IEnumerable<ChatMessage> messages)
        {
            LastToolResult = string.Join("|", messages.SelectMany(m => m.Contents).OfType<FunctionResultContent>()
                .Select(r => r.Exception?.ToString() ?? r.Result?.ToString()));
            return new ChatResponse(Reply());
        }

        public Task<ChatResponse> GetResponseAsync(IEnumerable<ChatMessage> messages, ChatOptions? options = null, CancellationToken ct = default) =>
            Task.FromResult(Capture(messages));

        public async IAsyncEnumerable<ChatResponseUpdate> GetStreamingResponseAsync(
            IEnumerable<ChatMessage> messages, ChatOptions? options = null, [EnumeratorCancellation] CancellationToken ct = default)
        {
            await Task.CompletedTask;
            var message = Reply();
            yield return new ChatResponseUpdate(message.Role, message.Contents);
        }

        public object? GetService(Type serviceType, object? serviceKey = null) => null;
        public void Dispose() { }
    }

    private sealed class StatusHandler(HttpStatusCode status, string body, string contentType = "application/json") : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct) =>
            Task.FromResult(new HttpResponseMessage(status) { Content = new StringContent(body, Encoding.UTF8, contentType) });
    }

    private HttpClient ClientFor(IChatClient model, bool authenticated = true, HttpMessageHandler? specialist = null)
    {
        var settings = new AgentSettings
        {
            AgentSharedSecret = new string('s', 48),
            JwtSecret = new string('j', 48),
            AuthMode = "local",
            A2aTransport = "legacy",
            AgentRegistry = """{"product-discovery":"http://fake-product-discovery"}""",
        };
        var server = OrchestratorTestHost.Create(
            _pool,
            r =>
            {
                r.MapChatRoutes();
                r.MapConversationRoutes();
            },
            settingsOverride: settings,
            configureServices: services =>
            {
                services.AddSingleton<IChatClient>(model);
                services.AddSingleton<AIAgent>(_ =>
                {
                    var a2a = new A2AClient(
                        new HttpClient(specialist ?? new StatusHandler(HttpStatusCode.OK, "{}")),
                        settings,
                        new AuthServerClient(new HttpClient(), settings),
                        NullLogger<A2AClient>.Instance);
                    var tools = new OrchestratorTools(a2a, settings, NullLogger<OrchestratorTools>.Instance);
                    return model.AsAIAgent(instructions: "route", name: "orchestrator", tools: tools.All().ToList());
                });
                services.AddSingleton<UsageRecorder>();
            });
        var client = server.CreateClient();
        if (authenticated)
        {
            client.DefaultRequestHeaders.Add("X-Test-Email", Email);
        }

        return client;
    }

    private async Task<string> SeedConversationAsync()
    {
        await using var conn = await _pool.OpenAsync();
        var userId = await conn.ExecuteScalarAsync<Guid>("SELECT id FROM users WHERE email = @email", new { email = Email });
        var id = await conn.ExecuteScalarAsync<Guid>("INSERT INTO conversations (user_id, title) VALUES (@u, 't') RETURNING id", new { u = userId });
        await conn.ExecuteAsync(
            "INSERT INTO messages (conversation_id, role, content) VALUES (@c, 'user', 'earlier'), (@c, 'assistant', 'ok')", new { c = id });
        return id.ToString();
    }

    private async Task<List<string>> ContentsAsync(string conversationId)
    {
        await using var conn = await _pool.OpenAsync();
        return (await conn.QueryAsync<string>(
            "SELECT content FROM messages WHERE conversation_id = @c ORDER BY created_at, id", new { c = Guid.Parse(conversationId) })).ToList();
    }

    private static List<(string Event, string Data)> Frames(string body) =>
        body.Split("\n\n", StringSplitOptions.RemoveEmptyEntries)
            .Select(e =>
            {
                var lines = e.Split('\n');
                return (
                    lines.FirstOrDefault(l => l.StartsWith("event: "))?["event: ".Length..] ?? "",
                    string.Join("\n", lines.Where(l => l.StartsWith("data: ")).Select(l => l["data: ".Length..])));
            }).ToList();

    private async Task AssertNothingPersistedAsync()
    {
        await using var conn = await _pool.OpenAsync();
        (await conn.ExecuteScalarAsync<long>("SELECT COUNT(*) FROM usage_logs")).Should().Be(0, "a missed turn logs no usage");
        (await conn.ExecuteScalarAsync<long>("SELECT COUNT(*) FROM messages WHERE role = 'assistant' AND content <> 'ok'"))
            .Should().Be(0, "a missed turn saves no assistant message");
    }

    // ─────────────────────── blocking /api/chat ───────────────────────

    [Fact]
    public async Task Blocking_OwnModelMiss_ReturnsAStructuredMissAndRemovesOnlyTheMissedMessage()
    {
        var conversationId = await SeedConversationAsync();
        using var client = ClientFor(new MissingModel());

        var response = await client.PostAsJsonAsync("/api/chat", new { message = "unrecorded question", conversation_id = conversationId });

        response.StatusCode.Should().Be(HttpStatusCode.OK);
        var payload = await response.Content.ReadFromJsonAsync<JsonElement>();
        var message = payload.GetProperty("replay_miss").GetProperty("message").GetString();
        message.Should().Be(ReplayMiss.DefaultMessage);
        payload.GetProperty("response").GetString().Should().Be(message);
        payload.GetProperty("conversation_id").GetString().Should().Be(conversationId);
        payload.GetProperty("agents_involved").EnumerateArray().Select(e => e.GetString()).Should().Equal("orchestrator");
        (await ContentsAsync(conversationId)).Should().BeEquivalentTo("earlier", "ok");
        await AssertNothingPersistedAsync();
    }

    [Fact]
    public async Task Blocking_ANormalTurnCarriesNoMiss()
    {
        using var client = ClientFor(new FakeChatClient().EnqueueResponse("hi"));

        var payload = await (await client.PostAsJsonAsync("/api/chat", new { message = "hello" })).Content.ReadFromJsonAsync<JsonElement>();

        (!payload.TryGetProperty("replay_miss", out var miss) || miss.ValueKind == JsonValueKind.Null)
            .Should().BeTrue("the field is null (as Python's is) or absent on a normal turn");
    }

    [Fact]
    public async Task Blocking_AnonymousMiss_IsReportedAndWritesNothing()
    {
        using var client = ClientFor(new MissingModel(), authenticated: false);

        var response = await client.PostAsJsonAsync("/api/chat", new { message = "unrecorded" });

        response.StatusCode.Should().Be(HttpStatusCode.OK);
        var payload = await response.Content.ReadFromJsonAsync<JsonElement>();
        payload.GetProperty("replay_miss").GetProperty("message").GetString().Should().Be(ReplayMiss.DefaultMessage);
        await using var conn = await _pool.OpenAsync();
        (await conn.ExecuteScalarAsync<long>("SELECT COUNT(*) FROM messages")).Should().Be(0);
    }

    [Fact]
    public async Task Blocking_SpecialistMissOverTheLegacyWire_EndsTheTurnWithoutAskingTheModelAgain()
    {
        var conversationId = await SeedConversationAsync();
        var model = new RoutingModel();
        using var client = ClientFor(model, specialist: new StatusHandler((HttpStatusCode)424, """{"error":"replay_miss"}"""));

        var response = await client.PostAsJsonAsync("/api/chat", new { message = "tents?", conversation_id = conversationId });

        response.StatusCode.Should().Be(HttpStatusCode.OK);
        var payload = await response.Content.ReadFromJsonAsync<JsonElement>();
        payload.GetProperty("replay_miss").GetProperty("message").GetString().Should().Be(ReplayMiss.DefaultMessage);
        payload.GetProperty("response").GetString().Should().NotContain("answered the miss");
        model.Calls.Should().Be(1, "the model is not shown a miss as if it were a tool result");
        (await ContentsAsync(conversationId)).Should().BeEquivalentTo("earlier", "ok");
        await AssertNothingPersistedAsync();
    }

    // ─────────────────────── streaming /api/chat/stream ───────────────────────

    [Fact]
    public async Task Stream_OwnModelMiss_SendsTheMissFrameThenMetadataAndDoneAndRemovesTheMessage()
    {
        var conversationId = await SeedConversationAsync();
        using var client = ClientFor(new MissingModel());

        var response = await client.PostAsJsonAsync("/api/chat/stream", new { message = "unrecorded question", conversation_id = conversationId });

        response.StatusCode.Should().Be(HttpStatusCode.OK);
        var frames = Frames(await response.Content.ReadAsStringAsync());
        frames.Select(f => f.Event).Should().Equal("replay_miss", "metadata", "done");
        JsonSerializer.Deserialize<JsonElement>(frames[0].Data).GetProperty("message").GetString().Should().Be(ReplayMiss.DefaultMessage);
        var metadata = JsonSerializer.Deserialize<JsonElement>(frames[1].Data);
        metadata.GetProperty("conversation_id").GetString().Should().Be(conversationId);
        metadata.GetProperty("agents_involved").EnumerateArray().Select(e => e.GetString()).Should().Equal("orchestrator");
        frames[2].Data.Should().Be("[DONE]");
        (await ContentsAsync(conversationId)).Should().BeEquivalentTo("earlier", "ok");
        await AssertNothingPersistedAsync();
    }

    [Fact]
    public async Task Stream_AnonymousMiss_IsReported()
    {
        using var client = ClientFor(new MissingModel(), authenticated: false);

        var frames = Frames(await (await client.PostAsJsonAsync("/api/chat/stream", new { message = "unrecorded" })).Content.ReadAsStringAsync());

        frames.Select(f => f.Event).Should().Equal("replay_miss", "metadata", "done");
    }

    [Fact]
    public async Task Stream_SpecialistMissOverTheLegacyWire_EndsTheTurnWithoutAskingTheModelAgain()
    {
        var conversationId = await SeedConversationAsync();
        var model = new RoutingModel();
        var sse = "event: replay_miss\ndata: {\"error\":\"replay_miss\"}\n\ndata: [DONE]\n\n";
        using var client = ClientFor(model, specialist: new StatusHandler(HttpStatusCode.OK, sse, "text/event-stream"));

        var response = await client.PostAsJsonAsync("/api/chat/stream", new { message = "tents?", conversation_id = conversationId });

        var body = await response.Content.ReadAsStringAsync();
        Frames(body).Select(f => f.Event).Should().Equal("replay_miss", "metadata", "done");
        body.Should().NotContain("answered the miss").And.NotContain("\"error\":\"replay_miss\"");
        model.Calls.Should().Be(1);
        (await ContentsAsync(conversationId)).Should().BeEquivalentTo("earlier", "ok");
        await AssertNothingPersistedAsync();
    }

    [Fact]
    public async Task Stream_AnotherSpecialistFailure_IsNotMistakenForAMiss()
    {
        // A specialist 500 is the ordinary "agent returned an error" sentence, which the model reads and answers.
        var model = new RoutingModel();
        using var client = ClientFor(model, specialist: new StatusHandler(HttpStatusCode.InternalServerError, "{}"));

        var body = await (await client.PostAsJsonAsync("/api/chat/stream", new { message = "tents?" })).Content.ReadAsStringAsync();

        body.Should().NotContain("event: replay_miss");
        model.Calls.Should().Be(2);
    }
}
