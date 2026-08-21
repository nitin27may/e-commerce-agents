using Dapper;
using ECommerceAgents.Orchestrator.Routes;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Telemetry;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using Microsoft.AspNetCore.Routing;
using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;
using Microsoft.Extensions.DependencyInjection;
using System.Net;
using System.Net.Http.Json;
using System.Text;
using System.Text.Json;
using Xunit;

namespace ECommerceAgents.Orchestrator.Tests;

/// <summary>
/// <see cref="ChatRoutes"/>'s conversation persistence — the core Phase 2
/// fix. Previously the .NET orchestrator never wrote to
/// <c>conversations</c>/<c>messages</c> at all, so <c>GET /api/conversations</c>
/// was permanently empty and the response shape omitted <c>conversation_id</c>/
/// <c>agents_involved</c> that the frontend's TS types require as non-optional.
/// Uses a real Postgres testcontainer (via <see cref="LocalPostgresCollection"/>,
/// already defined in <see cref="OrchestratorRouteTests"/>) and a
/// <see cref="FakeChatClient"/>-backed <c>AIAgent</c> — no real LLM call.
/// </summary>
[Collection(nameof(LocalPostgresCollection))]
public sealed class ChatRoutesTests : IAsyncLifetime
{
    private readonly PostgresFixture _pg;
    private DatabasePool _pool = null!;
    private const string Email = "chatroutes@example.com";

    public ChatRoutesTests(PostgresFixture pg) => _pg = pg;

    public async Task InitializeAsync()
    {
        var settings = new AgentSettings { DatabaseUrl = _pg.ConnectionString };
        _pool = new DatabasePool(settings);

        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            @"TRUNCATE order_status_history, order_items, returns, orders,
                       messages, conversations, warehouse_inventory,
                       warehouses, reviews, products, users, usage_logs
              RESTART IDENTITY CASCADE"
        );
        await conn.ExecuteAsync(
            "INSERT INTO users (email, password_hash, name, role) VALUES (@email, 'x', 'Chat Tester', 'customer')",
            new { email = Email }
        );
    }

    public async Task DisposeAsync() => await _pool.DisposeAsync();

    private HttpClient ClientFor(
        FakeChatClient chatClient,
        bool authenticated = true,
        AgentSettings? settings = null
    )
    {
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
                services.AddSingleton<IChatClient>(chatClient);
                services.AddSingleton<AIAgent>(sp =>
                    sp.GetRequiredService<IChatClient>().AsAIAgent(instructions: "test instructions", name: "orchestrator")
                );
                services.AddSingleton<UsageRecorder>();
            }
        );
        var client = server.CreateClient();
        if (authenticated)
        {
            client.DefaultRequestHeaders.Add("X-Test-Email", Email);
        }
        return client;
    }

    // ─────────────────────── blocking chat ───────────────────

    [Fact]
    public async Task SendAsync_Authenticated_CreatesConversationAndPersistsBothMessages()
    {
        using var client = ClientFor(new FakeChatClient().EnqueueResponse("Hi! Here are some headphones."));

        var response = await client.PostAsJsonAsync("/api/chat", new { message = "Find me headphones" });
        response.EnsureSuccessStatusCode();
        var payload = await response.Content.ReadFromJsonAsync<JsonElement>();

        payload.GetProperty("response").GetString().Should().Be("Hi! Here are some headphones.");
        var conversationId = payload.GetProperty("conversation_id").GetString();
        conversationId.Should().NotBeNullOrEmpty();
        payload.GetProperty("agents_involved").EnumerateArray().Select(e => e.GetString())
            .Should().Equal("orchestrator");

        var convos = await client.GetFromJsonAsync<JsonElement>("/api/conversations");
        convos.GetArrayLength().Should().Be(1);
        convos[0].GetProperty("id").GetString().Should().Be(conversationId);
        convos[0].GetProperty("message_count").GetInt32().Should().Be(2); // user + assistant
    }

    [Fact]
    public async Task SendAsync_SecondTurn_AppendsToSameConversation()
    {
        using var client = ClientFor(
            new FakeChatClient().EnqueueResponse("first reply").EnqueueResponse("second reply")
        );

        var first = await client.PostAsJsonAsync("/api/chat", new { message = "hello" });
        var firstPayload = await first.Content.ReadFromJsonAsync<JsonElement>();
        var conversationId = firstPayload.GetProperty("conversation_id").GetString();

        var second = await client.PostAsJsonAsync(
            "/api/chat",
            new { message = "follow up", conversation_id = conversationId }
        );
        second.EnsureSuccessStatusCode();
        var secondPayload = await second.Content.ReadFromJsonAsync<JsonElement>();
        secondPayload.GetProperty("conversation_id").GetString().Should().Be(conversationId);

        var detail = await client.GetFromJsonAsync<JsonElement>($"/api/conversations/{conversationId}");
        detail.GetProperty("messages").GetArrayLength().Should().Be(4); // 2 user + 2 assistant turns
    }

    [Fact]
    public async Task SendAsync_FirstTurn_SendsSingleMessageHistoryToAgent()
    {
        var chatClient = new FakeChatClient().EnqueueResponse("first reply");
        using var client = ClientFor(chatClient);

        await client.PostAsJsonAsync("/api/chat", new { message = "hello" });

        chatClient.ReceivedMessages.Should().HaveCount(1);
        var messages = chatClient.ReceivedMessages[0].ToList();
        messages.Should().HaveCount(1);
        messages[0].Role.Should().Be(ChatRole.User);
        messages[0].Text.Should().Be("hello");
    }

    [Fact]
    public async Task SendAsync_SecondTurn_ForwardsFullPriorHistoryToAgent()
    {
        var chatClient = new FakeChatClient().EnqueueResponse("first reply").EnqueueResponse("second reply");
        using var client = ClientFor(chatClient);

        var first = await client.PostAsJsonAsync("/api/chat", new { message = "hello" });
        var firstPayload = await first.Content.ReadFromJsonAsync<JsonElement>();
        var conversationId = firstPayload.GetProperty("conversation_id").GetString();

        await client.PostAsJsonAsync(
            "/api/chat",
            new { message = "follow up", conversation_id = conversationId }
        );

        chatClient.ReceivedMessages.Should().HaveCount(2);
        var secondCallMessages = chatClient.ReceivedMessages[1].ToList();
        secondCallMessages.Select(m => (m.Role, m.Text)).Should().Equal(
            (ChatRole.User, "hello"),
            (ChatRole.Assistant, "first reply"),
            (ChatRole.User, "follow up")
        );
    }

    [Fact]
    public async Task SendAsync_BindsTheSessionIdToTheConversation()
    {
        // #9. CurrentSessionId is otherwise only ever set from an inbound
        // X-Session-Id header, and the browser never sends one — so every A2A
        // call forwarded an empty session id and HistoryRehydrator could never
        // find anything. Harmless on this stack today only because
        // OrchestratorTools still passes history in the body; the fallback was
        // dead code, and the matching bug on Python broke every follow-up.
        var chatClient = new FakeChatClient().EnqueueResponse("reply");
        string? seen = null;
        chatClient.OnCall = () => seen = RequestContext.CurrentSessionId;
        using var client = ClientFor(chatClient);

        var resp = await client.PostAsJsonAsync("/api/chat", new { message = "hello" });
        var payload = await resp.Content.ReadFromJsonAsync<JsonElement>();

        seen.Should().Be(payload.GetProperty("conversation_id").GetString());
        seen.Should().NotBeNullOrEmpty();
    }

    [Fact]
    public async Task SendAsync_Anonymous_DoesNotBindAClientSuppliedConversationId()
    {
        // The anonymous branch takes conversation_id straight from the request
        // body with no ownership check, so binding it would let anyone read any
        // conversation by UUID via the specialist's rehydration path.
        var chatClient = new FakeChatClient().EnqueueResponse("anonymous reply");
        string? seen = null;
        chatClient.OnCall = () => seen = RequestContext.CurrentSessionId;
        using var client = ClientFor(chatClient, authenticated: false);

        await client.PostAsJsonAsync(
            "/api/chat",
            new { message = "what did we discuss?", conversation_id = Guid.NewGuid().ToString() }
        );

        seen.Should().BeEmpty();
    }

    [Fact]
    public async Task SendAsync_Anonymous_SendsSingleMessageHistoryToAgent()
    {
        var chatClient = new FakeChatClient().EnqueueResponse("anonymous reply");
        using var client = ClientFor(chatClient, authenticated: false);

        await client.PostAsJsonAsync("/api/chat", new { message = "browsing without an account" });

        chatClient.ReceivedMessages.Should().HaveCount(1);
        var messages = chatClient.ReceivedMessages[0].ToList();
        messages.Should().HaveCount(1);
        messages[0].Role.Should().Be(ChatRole.User);
        messages[0].Text.Should().Be("browsing without an account");
    }

    [Fact]
    public async Task SendAsync_UnknownConversationId_ReturnsNotFound()
    {
        using var client = ClientFor(new FakeChatClient().EnqueueResponse("unused"));

        var response = await client.PostAsJsonAsync(
            "/api/chat",
            new { message = "hi", conversation_id = Guid.NewGuid().ToString() }
        );

        response.StatusCode.Should().Be(HttpStatusCode.NotFound);
    }

    [Fact]
    public async Task SendAsync_Anonymous_PersistsNothingButStillResponds()
    {
        using var client = ClientFor(new FakeChatClient().EnqueueResponse("anonymous reply"), authenticated: false);

        var response = await client.PostAsJsonAsync("/api/chat", new { message = "browsing without an account" });
        response.EnsureSuccessStatusCode();
        var payload = await response.Content.ReadFromJsonAsync<JsonElement>();
        payload.GetProperty("response").GetString().Should().Be("anonymous reply");
        payload.GetProperty("conversation_id").GetString().Should().BeEmpty();

        await using var conn = await _pool.OpenAsync();
        var conversationCount = await conn.ExecuteScalarAsync<long>("SELECT COUNT(*) FROM conversations");
        var messageCount = await conn.ExecuteScalarAsync<long>("SELECT COUNT(*) FROM messages");
        conversationCount.Should().Be(0);
        messageCount.Should().Be(0);
    }

    [Fact]
    public async Task SendAsync_Authenticated_LogsUsage()
    {
        using var client = ClientFor(new FakeChatClient().EnqueueResponse("logged reply"));

        await client.PostAsJsonAsync("/api/chat", new { message = "log this" });

        await using var conn = await _pool.OpenAsync();
        var status = await conn.ExecuteScalarAsync<string?>(
            "SELECT status FROM usage_logs WHERE agent_name = 'orchestrator'"
        );
        status.Should().Be("success");
    }

    // ─────────────────────── streaming chat ──────────────────

    [Fact]
    public async Task StreamAsync_EmitsMetadataEventAndPersistsAssistantMessage()
    {
        using var client = ClientFor(new FakeChatClient().EnqueueResponse("streamed reply"));

        var response = await client.PostAsJsonAsync("/api/chat/stream", new { message = "stream this" });
        response.EnsureSuccessStatusCode();
        var body = await response.Content.ReadAsStringAsync();

        body.Should().Contain("event: metadata");
        body.Should().Contain("\"agents_involved\":[\"orchestrator\"]");
        body.Should().Contain("data: [DONE]");

        // Parse the conversation_id out of the metadata frame the same way the
        // frontend does, and confirm the turn was actually persisted.
        var metadataLine = body.Split("\n\n").First(e => e.Contains("event: metadata"));
        var dataLine = metadataLine.Split('\n').First(l => l.StartsWith("data: "))["data: ".Length..];
        var metadata = JsonSerializer.Deserialize<JsonElement>(dataLine);
        var conversationId = metadata.GetProperty("conversation_id").GetString();
        conversationId.Should().NotBeNullOrEmpty();

        var detail = await client.GetFromJsonAsync<JsonElement>($"/api/conversations/{conversationId}");
        detail.GetProperty("messages").GetArrayLength().Should().Be(2);
    }

    [Fact]
    public async Task StreamAsync_PreservesEmbeddedNewlinesInSseFraming()
    {
        // Regression: a naive `data: {chunk}\n\n` line breaks once chunk itself
        // contains real newlines — the frontend's event boundary is any "\n\n" in
        // the buffer (web/src/lib/api.ts::chatStream), so an embedded "\n\n" (or a
        // lone-newline delta arriving as its own chunk) gets misread as ending the
        // event early, silently dropping the newline from the reconstructed
        // message. Chunks must be sent as proper SSE multi-line data (one
        // "data: <line>" per line, spec §9.2.6) so the frontend's own
        // dataParts.join("\n") reconstructs the original text exactly — including
        // markdown list separators and the blank line between a ```product fence
        // and its JSON body.
        var original = "Highlights:\n- Over-ear design\n- 30 hour battery\n\n```product\n{\"name\":\"Test\"}\n```\n\nWant more?";
        using var client = ClientFor(new FakeChatClient().EnqueueResponse(original));

        var response = await client.PostAsJsonAsync("/api/chat/stream", new { message = "tell me" });
        response.EnsureSuccessStatusCode();
        var body = await response.Content.ReadAsStringAsync();

        ReconstructTextFromSse(body).Should().Be(original);
    }

    /// <summary>Mirrors web/src/lib/api.ts::chatStream's SSE parsing exactly, so
    /// this test fails the same way a real browser client would regress.</summary>
    private static string ReconstructTextFromSse(string body)
    {
        var text = new StringBuilder();
        foreach (var evt in body.Split("\n\n"))
        {
            if (evt.Length == 0) continue;
            var lines = evt.Split('\n');
            var eventType = "";
            var dataParts = new List<string>();
            foreach (var line in lines)
            {
                if (line.StartsWith("event: "))
                {
                    eventType = line["event: ".Length..].Trim();
                }
                else if (line.StartsWith("data: "))
                {
                    dataParts.Add(line["data: ".Length..]);
                }
            }
            if (dataParts.Count == 0) continue;
            var data = string.Join("\n", dataParts);
            if (data == "[DONE]" || eventType is "step" or "metadata") continue;
            text.Append(data);
        }
        return text.ToString();
    }

    [Fact]
    public async Task StreamAsync_SecondTurn_ForwardsFullPriorHistoryToAgent()
    {
        var chatClient = new FakeChatClient().EnqueueResponse("first reply").EnqueueResponse("second reply");
        using var client = ClientFor(chatClient);

        var first = await client.PostAsJsonAsync("/api/chat/stream", new { message = "hello" });
        var firstBody = await first.Content.ReadAsStringAsync();
        var firstMetaLine = firstBody.Split("\n\n").First(e => e.Contains("event: metadata"));
        var firstData = firstMetaLine.Split('\n').First(l => l.StartsWith("data: "))["data: ".Length..];
        var conversationId = JsonSerializer.Deserialize<JsonElement>(firstData).GetProperty("conversation_id").GetString();

        await client.PostAsJsonAsync(
            "/api/chat/stream",
            new { message = "follow up", conversation_id = conversationId }
        );

        chatClient.ReceivedMessages.Should().HaveCount(2);
        var secondCallMessages = chatClient.ReceivedMessages[1].ToList();
        secondCallMessages.Select(m => (m.Role, m.Text)).Should().Equal(
            (ChatRole.User, "hello"),
            (ChatRole.Assistant, "first reply"),
            (ChatRole.User, "follow up")
        );
    }

    [Fact]
    public async Task StreamAsync_Anonymous_PersistsNothing()
    {
        using var client = ClientFor(new FakeChatClient().EnqueueResponse("anon stream reply"), authenticated: false);

        var response = await client.PostAsJsonAsync("/api/chat/stream", new { message = "anon streaming" });
        response.EnsureSuccessStatusCode();

        await using var conn = await _pool.OpenAsync();
        var messageCount = await conn.ExecuteScalarAsync<long>("SELECT COUNT(*) FROM messages");
        messageCount.Should().Be(0);
    }

    // ─────────────── orchestration mode (#33 PR 3) ───────────────

    /// <summary>
    /// The frontend sends `mode` on every request. Before ChatRequest had a
    /// Mode property, System.Text.Json dropped it: selecting a workflow mode
    /// produced a plain tool-router run with no signal that anything had been
    /// ignored. Refusing is strictly better than answering a different
    /// question silently.
    /// </summary>
    [Fact]
    public async Task SendAsync_UnsupportedMode_IsRejectedRatherThanSilentlyDowngraded()
    {
        var chatClient = new FakeChatClient().EnqueueResponse("should never be reached");
        using var client = ClientFor(chatClient);

        var response = await client.PostAsJsonAsync(
            "/api/chat",
            new { message = "Should I buy this?", mode = "workflow:pre-purchase" }
        );

        response.StatusCode.Should().Be(HttpStatusCode.BadRequest);
        var payload = await response.Content.ReadFromJsonAsync<JsonElement>();
        payload.GetProperty("requested_mode").GetString().Should().Be("workflow:pre-purchase");
        payload.GetProperty("supported_modes").EnumerateArray()
            .Select(e => e.GetString()).Should().Contain("tool");

        chatClient.CallCount.Should().Be(0, "an unsupported mode must not reach the model");
    }

    [Fact]
    public async Task StreamAsync_UnsupportedMode_IsRejectedRatherThanSilentlyDowngraded()
    {
        var chatClient = new FakeChatClient().EnqueueResponse("should never be reached");
        using var client = ClientFor(chatClient);

        var response = await client.PostAsJsonAsync(
            "/api/chat/stream",
            new { message = "Should I buy this?", mode = "group-chat" }
        );

        response.StatusCode.Should().Be(HttpStatusCode.BadRequest);
        chatClient.CallCount.Should().Be(0);
    }

    [Fact]
    public async Task SendAsync_SupportedMode_IsAccepted()
    {
        var chatClient = new FakeChatClient().EnqueueResponse("routed");
        using var client = ClientFor(chatClient);

        var response = await client.PostAsJsonAsync("/api/chat", new { message = "hi", mode = "tool" });

        response.EnsureSuccessStatusCode();
        chatClient.CallCount.Should().Be(1);
    }

    [Fact]
    public async Task SendAsync_NoMode_StillWorks()
    {
        // Omitting mode must stay valid — the storefront assistant and every
        // existing client send no mode at all.
        var chatClient = new FakeChatClient().EnqueueResponse("default");
        using var client = ClientFor(chatClient);

        var response = await client.PostAsJsonAsync("/api/chat", new { message = "hi" });

        response.EnsureSuccessStatusCode();
        chatClient.CallCount.Should().Be(1);
    }

    // ─────────────────────── grounding (#33 PR 7) ───────────────────

    /// <summary>
    /// Seeds one real product and returns an answer whose card cites it, so a
    /// grounding run has something genuinely verifiable to check.
    /// </summary>
    private async Task<Guid> SeedProductAsync(decimal price = 100.00m)
    {
        await using var conn = await _pool.OpenAsync();
        return await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO products (name, description, category, brand, price)
              VALUES ('Grounded Product', 'd', 'Electronics', 'Acme', @price) RETURNING id",
            new { price }
        );
    }

    private static string CardAnswer(Guid id, decimal price) =>
        $$"""
        Here you go:

        ```product
        {"id": "{{id}}", "name": "Grounded Product", "price": {{price}}}
        ```
        """;

    [Fact]
    public async Task SendAsync_AnnotateMode_AttachesTheGroundingReport()
    {
        var id = await SeedProductAsync();
        using var client = ClientFor(
            new FakeChatClient().EnqueueResponse(CardAnswer(id, 100.00m)),
            settings: new AgentSettings { GroundingMode = "annotate" }
        );

        var response = await client.PostAsJsonAsync("/api/chat", new { message = "find a product" });
        var payload = await response.Content.ReadFromJsonAsync<JsonElement>();

        var grounding = payload.GetProperty("grounding");
        grounding.GetProperty("verified").GetInt32().Should().Be(1);
        grounding.GetProperty("unverified").GetInt32().Should().Be(0);
    }

    /// <summary>
    /// A card citing a product id that does not exist is the failure grounding
    /// exists to surface — it renders perfectly and 404s downstream.
    /// </summary>
    [Fact]
    public async Task SendAsync_ReportsAFabricatedProductAsUnverified()
    {
        using var client = ClientFor(
            new FakeChatClient().EnqueueResponse(CardAnswer(Guid.NewGuid(), 100.00m)),
            settings: new AgentSettings { GroundingMode = "annotate" }
        );

        var response = await client.PostAsJsonAsync("/api/chat", new { message = "find a product" });
        var payload = await response.Content.ReadFromJsonAsync<JsonElement>();

        var grounding = payload.GetProperty("grounding");
        grounding.GetProperty("verified").GetInt32().Should().Be(0);
        grounding.GetProperty("claims")[0].GetProperty("status").GetString().Should().Be("not_found");
    }

    /// <summary>
    /// observe verifies but attaches nothing, so a deployment can measure
    /// grounding before showing it to users — matching Python, where only
    /// annotate/enforce attach the report.
    /// </summary>
    [Fact]
    public async Task SendAsync_ObserveMode_VerifiesButAttachesNothing()
    {
        var id = await SeedProductAsync();
        using var client = ClientFor(
            new FakeChatClient().EnqueueResponse(CardAnswer(id, 100.00m)),
            settings: new AgentSettings { GroundingMode = "observe" }
        );

        var response = await client.PostAsJsonAsync("/api/chat", new { message = "find a product" });
        var payload = await response.Content.ReadFromJsonAsync<JsonElement>();

        payload.TryGetProperty("grounding", out var grounding).Should().BeTrue();
        grounding.ValueKind.Should().Be(JsonValueKind.Null);
    }

    [Fact]
    public async Task SendAsync_OffMode_SkipsGroundingEntirely()
    {
        var id = await SeedProductAsync();
        using var client = ClientFor(
            new FakeChatClient().EnqueueResponse(CardAnswer(id, 100.00m)),
            settings: new AgentSettings { GroundingMode = "off" }
        );

        var response = await client.PostAsJsonAsync("/api/chat", new { message = "find a product" });
        var payload = await response.Content.ReadFromJsonAsync<JsonElement>();

        payload.GetProperty("grounding").ValueKind.Should().Be(JsonValueKind.Null);
    }

    /// <summary>
    /// A chat turn that claims nothing checkable must carry no report at all —
    /// a badge reading "0 facts verified" on a greeting reads as a failure.
    /// </summary>
    [Fact]
    public async Task SendAsync_AnAnswerWithNoClaims_CarriesNoReport()
    {
        using var client = ClientFor(
            new FakeChatClient().EnqueueResponse("Hi! How can I help?"),
            settings: new AgentSettings { GroundingMode = "annotate" }
        );

        var response = await client.PostAsJsonAsync("/api/chat", new { message = "hello" });
        var payload = await response.Content.ReadFromJsonAsync<JsonElement>();

        payload.GetProperty("grounding").ValueKind.Should().Be(JsonValueKind.Null);
    }

    [Fact]
    public async Task StreamAsync_EmitsAGroundingFrame_ForAGroundedAnswer()
    {
        var id = await SeedProductAsync();
        using var client = ClientFor(
            new FakeChatClient().EnqueueResponse(CardAnswer(id, 100.00m)),
            settings: new AgentSettings { GroundingMode = "annotate" }
        );

        var response = await client.PostAsJsonAsync("/api/chat/stream", new { message = "find a product" });
        var body = await response.Content.ReadAsStringAsync();

        // The client renders the badge off this frame; without it the badge
        // never appears on a streamed turn, which is every turn in the UI.
        body.Should().Contain("event: grounding");
        var frame = body
            .Split('\n')
            .SkipWhile(l => !l.StartsWith("event: grounding"))
            .First(l => l.StartsWith("data: "))["data: ".Length..];
        JsonDocument.Parse(frame).RootElement.GetProperty("verified").GetInt32().Should().Be(1);
    }
}
