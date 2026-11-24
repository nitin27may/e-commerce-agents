using System.ComponentModel;
using System.Runtime.CompilerServices;
using System.Text.Json;
using Dapper;
using ECommerceAgents.Shared.A2A;
using ECommerceAgents.Shared.Agents;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Guardrails;
using ECommerceAgents.Shared.Middleware;
using ECommerceAgents.Shared.Tools;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using Microsoft.Agents.AI;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.TestHost;
using Microsoft.Extensions.AI;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging.Abstractions;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// A gated call's refusal or pending sentence reaches the user from the host, not from the model's paraphrase of a tool result,
/// as in Python's <c>park_pending_approvals</c>. Real <see cref="SpecialistPipeline"/>, real <see cref="HitlGate"/>, real
/// Postgres (testcontainers); only the model is scripted.
/// </summary>
[Collection(nameof(LocalPostgresCollection))]
public sealed class GateNoticeTests : IAsyncLifetime
{
    private const string Customer = "jack.anderson@gmail.com";
    private const string Agent = "order-management";

    private readonly PostgresFixture _pg;
    private DatabasePool _pool = null!;
    private AgentSettings _settings = null!;

    public GateNoticeTests(PostgresFixture pg) => _pg = pg;

    public async Task InitializeAsync()
    {
        _settings = new AgentSettings { DatabaseUrl = _pg.ConnectionString, HitlEnabled = true };
        _pool = new DatabasePool(_settings);
        await ResetAsync();
    }

    public async Task DisposeAsync()
    {
        await ResetAsync();
        await _pool.DisposeAsync();
    }

    private async Task ResetAsync()
    {
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            "TRUNCATE tool_approval_requests, approvals, order_status_history, returns, order_items, orders, warehouse_inventory, warehouses, products, users RESTART IDENTITY CASCADE");
    }

    // ───────────────────────── the scripted model and the gated tools ─────────────────────────

    /// <summary>Calls each listed tool once, in one turn, then answers with <see cref="Final"/> whatever the tool results said.</summary>
    private sealed class GatedCallModel(string final, params (string Tool, string Arg, string Value)[] calls) : IChatClient
    {
        public List<string> ToolResults { get; } = [];

        public async Task<ChatResponse> GetResponseAsync(
            IEnumerable<ChatMessage> messages, ChatOptions? options = null, CancellationToken cancellationToken = default)
        {
            var updates = new List<ChatResponseUpdate>();
            await foreach (var update in GetStreamingResponseAsync(messages, options, cancellationToken))
            {
                updates.Add(update);
            }

            return updates.ToChatResponse();
        }

        public async IAsyncEnumerable<ChatResponseUpdate> GetStreamingResponseAsync(
            IEnumerable<ChatMessage> messages,
            ChatOptions? options = null,
            [EnumeratorCancellation] CancellationToken cancellationToken = default)
        {
            var results = messages.SelectMany(m => m.Contents.OfType<FunctionResultContent>()).ToList();
            if (results.Count == 0 && calls.Length > 0)
            {
                var contents = calls
                    .Select((c, i) => (AIContent)new FunctionCallContent($"c{i}", c.Tool, new Dictionary<string, object?> { [c.Arg] = c.Value }))
                    .ToList();
                yield return new ChatResponseUpdate(ChatRole.Assistant, contents);
                yield break;
            }

            foreach (var result in results)
            {
                ToolResults.Add(JsonSerializer.Serialize(result.Result));
            }

            if (final.Length > 0)
            {
                yield return new ChatResponseUpdate(ChatRole.Assistant, final);
            }

            await Task.CompletedTask;
        }

        public object? GetService(Type serviceType, object? serviceKey = null) => null;

        public void Dispose()
        {
        }
    }

    /// <summary>Gated by name. The bodies must never run on a refused or parked call.</summary>
    private sealed class GatedTools
    {
        public int Ran;

        [Description("Cancel an order.")]
        public Task<string> CancelOrder([Description("the order id")] string orderId)
        {
            Ran++;
            return Task.FromResult("cancelled");
        }

        [RequiresRole("seller", "admin")]
        [Description("Place a backorder.")]
        public Task<string> PlaceBackorder([Description("the product id")] string productId)
        {
            Ran++;
            return Task.FromResult("backordered");
        }

        [Description("Look something up.")]
        public Task<string> Lookup([Description("a sku")] string sku) => Task.FromResult($"{sku}=79.99");

        public IEnumerable<AITool> All() =>
        [
            AgentTool.Create(CancelOrder, nameof(CancelOrder)),
            AgentTool.Create(PlaceBackorder, nameof(PlaceBackorder)),
            AgentTool.Create(Lookup, nameof(Lookup)),
        ];
    }

    private IServiceProvider Services(GatedCallModel model, GatedTools tools)
    {
        var services = new ServiceCollection();
        services.AddLogging();
        services.AddSingleton(_settings);
        services.AddSingleton(_pool);
        services.AddSingleton<AgentRunLogger>();
        services.AddSingleton<ToolAuditMiddleware>();
        services.AddSingleton<PiiRedactor>();
        services.AddSingleton<HitlGate>();
        services.AddSingleton<AIAgent>(sp =>
        {
            var options = SpecialistAgentFactory.BuildOptions(_settings, "You are a test specialist.", Agent, tools.All());
            return SpecialistPipeline.Apply(model.AsAIAgent(options), _settings, sp, Agent);
        });
        return services.BuildServiceProvider();
    }

    private static IDisposable AsCustomer(string email = Customer, string role = "customer") =>
        RequestContext.Scope(email, role, "", new List<HistoryEntry> { new("user", "earlier turn") });

    private static async Task<List<string>> StreamAsync(IServiceProvider services)
    {
        var chunks = new List<string>();
        await foreach (var chunk in AgentHost.RunAgentWithHistoryStreamingAsync(services, "please do it"))
        {
            chunks.Add(chunk);
        }

        return chunks;
    }

    private async Task<Guid> OrderAsync(string status)
    {
        await using var conn = await _pool.OpenAsync();
        var userId = await conn.ExecuteScalarAsync<Guid>(
            "INSERT INTO users (email, password_hash, name, role) VALUES (@email, 'x', 'T', 'customer') ON CONFLICT (email) DO UPDATE SET name = 'T' RETURNING id",
            new { email = Customer });
        return await conn.ExecuteScalarAsync<Guid>(
            "INSERT INTO orders (user_id, status, total, shipping_address) VALUES (@userId, @status, 25, '{}'::jsonb) RETURNING id",
            new { userId, status });
    }

    private async Task<long> RequestRowsAsync()
    {
        await using var conn = await _pool.OpenAsync();
        return await conn.ExecuteScalarAsync<long>("SELECT COUNT(*) FROM tool_approval_requests");
    }

    private static string Shipped(Guid id) =>
        $"Order {id} {GateRules.CancelBlocker("shipped")}, so it can no longer be cancelled. {ParkPrechecks.NotSubmitted}";

    // ───────────────────────── blocking path ─────────────────────────

    [Fact]
    public async Task Blocking_ShippedCancel_AppendsTheExactRefusal_EvenWhenTheModelClaimsSuccess()
    {
        var orderId = await OrderAsync("shipped");
        var model = new GatedCallModel("Done, I cancelled it for you.", ("cancel_order", "orderId", orderId.ToString()));
        var tools = new GatedTools();
        using var scope = AsCustomer();

        var text = await AgentHost.RunAgentWithHistoryAsync(Services(model, tools), "cancel it");

        text.Should().Be($"Done, I cancelled it for you.\n\n{Shipped(orderId)}");
        text.Should().EndWith(ParkPrechecks.NotSubmitted);
        tools.Ran.Should().Be(0);
        (await RequestRowsAsync()).Should().Be(0);
        // The model was told it was handled, in neutral words, not given the sentence to relay.
        model.ToolResults.Should().ContainSingle().Which.Should().Contain(HitlGate.HandledResultMessage).And.NotContain("shipped");
    }

    [Fact]
    public async Task Blocking_PlacedCancel_AppendsTheExactPendingSentence()
    {
        var orderId = await OrderAsync("placed");
        var model = new GatedCallModel("", ("cancel_order", "orderId", orderId.ToString()));
        var tools = new GatedTools();
        using var scope = AsCustomer();

        var text = await AgentHost.RunAgentWithHistoryAsync(Services(model, tools), "cancel it");

        await using var conn = await _pool.OpenAsync();
        var requestId = await conn.ExecuteScalarAsync<Guid>("SELECT id FROM tool_approval_requests");
        text.Should().Be(
            $"I have asked a team member to approve cancelling order {orderId} (ref: {requestId.ToString()[..8]}). " +
            "Nothing has changed yet; you will be notified once it has been reviewed.");
        tools.Ran.Should().Be(0);
    }

    [Fact]
    public async Task Blocking_TwoGatedCalls_AppendBothOnce_AndDuplicatesCollapse()
    {
        var orderId = await OrderAsync("shipped");
        var id = orderId.ToString();
        var model = new GatedCallModel(
            "All done.",
            ("cancel_order", "orderId", id),
            ("cancel_order", "orderId", id),
            ("place_backorder", "productId", Guid.NewGuid().ToString()));
        using var scope = AsCustomer();

        var text = await AgentHost.RunAgentWithHistoryAsync(Services(model, new GatedTools()), "do both");

        var role = "Your account is not permitted to place backorders; that is limited to admin, seller accounts. " + ParkPrechecks.NotSubmitted;
        var tail = text["All done.\n\n".Length..];
        text.Should().StartWith("All done.\n\n");
        tail.Split(Shipped(orderId)).Length.Should().Be(2, "the same refusal twice is shown once");
        tail.Should().Contain(role);
        tail.Should().Be($"{Shipped(orderId)} {role}", "one sentence per distinct call, joined by a space, in call order");
    }

    [Fact]
    public async Task Blocking_FailClosedSentences_AreAppendedToo()
    {
        // No identity: the gate fails closed and says so.
        var model = new GatedCallModel("Sure.", ("cancel_order", "orderId", Guid.NewGuid().ToString()));
        using (RequestContext.Scope("", "customer", "", new List<HistoryEntry> { new("user", "earlier turn") }))
        {
            var text = await AgentHost.RunAgentWithHistoryAsync(Services(model, new GatedTools()), "cancel");
            text.Should().Be($"Sure.\n\n{ParkPrechecks.ParkFailedReply}");
        }

        // A database error in the write: same sentence.
        var broken = new AgentSettings { DatabaseUrl = "Host=127.0.0.1;Port=1;Database=x;Username=x;Password=x;Timeout=1", HitlEnabled = true };
        var services = new ServiceCollection();
        services.AddLogging();
        services.AddSingleton(broken);
        var brokenPool = new DatabasePool(broken);
        services.AddSingleton(brokenPool);
        services.AddSingleton<AgentRunLogger>();
        services.AddSingleton<ToolAuditMiddleware>();
        services.AddSingleton<PiiRedactor>();
        services.AddSingleton<HitlGate>();
        var model2 = new GatedCallModel("Sure.", ("cancel_order", "orderId", Guid.NewGuid().ToString()));
        services.AddSingleton<AIAgent>(sp =>
            SpecialistPipeline.Apply(
                model2.AsAIAgent(SpecialistAgentFactory.BuildOptions(broken, "i", Agent, new GatedTools().All())), broken, sp, Agent));
        using var scope = AsCustomer();

        var failed = await AgentHost.RunAgentWithHistoryAsync(services.BuildServiceProvider(), "cancel");

        failed.Should().Be($"Sure.\n\n{ParkPrechecks.ParkFailedReply}");
        await brokenPool.DisposeAsync();
    }

    [Fact]
    public async Task Blocking_UngatedRun_IsUntouched()
    {
        var model = new GatedCallModel("The price is 79.99.", ("lookup", "sku", "XM5"));
        using var scope = AsCustomer();

        var text = await AgentHost.RunAgentWithHistoryAsync(Services(model, new GatedTools()), "price?");

        text.Should().Be("The price is 79.99.");
        model.ToolResults.Should().ContainSingle().Which.Should().Contain("79.99");
    }

    // ───────────────────────── streaming path ─────────────────────────

    [Fact]
    public async Task Streaming_RefusalIsTheLastChunk_AfterTheModelsOwnText()
    {
        var orderId = await OrderAsync("shipped");
        var model = new GatedCallModel("Done, I cancelled it for you.", ("cancel_order", "orderId", orderId.ToString()));
        using var scope = AsCustomer();

        var chunks = await StreamAsync(Services(model, new GatedTools()));

        chunks.Should().HaveCountGreaterThan(1);
        chunks[^1].Should().Be($"\n\n{Shipped(orderId)}");
        string.Concat(chunks[..^1]).Should().Be("Done, I cancelled it for you.");
    }

    [Fact]
    public async Task Streaming_WhenTheModelSaidNothing_TheSentenceIsTheOnlyChunk_WithoutALeadingBlankLine()
    {
        var orderId = await OrderAsync("shipped");
        var model = new GatedCallModel("", ("cancel_order", "orderId", orderId.ToString()));
        using var scope = AsCustomer();

        var chunks = await StreamAsync(Services(model, new GatedTools()));

        chunks.Should().Equal(Shipped(orderId));
    }

    [Fact]
    public async Task Streaming_TwoCalls_OneFinalChunkWithBothSentences()
    {
        var orderId = await OrderAsync("shipped");
        var model = new GatedCallModel(
            "All done.", ("cancel_order", "orderId", orderId.ToString()), ("place_backorder", "productId", Guid.NewGuid().ToString()));
        using var scope = AsCustomer();

        var chunks = await StreamAsync(Services(model, new GatedTools()));

        chunks[^1].Should().StartWith("\n\n" + Shipped(orderId)).And.Contain("not permitted to place backorders");
        chunks.Count(c => c.Contains(ParkPrechecks.NotSubmitted)).Should().Be(1);
    }

    [Fact]
    public async Task Streaming_WithoutARequestScope_DegradesToTheToolResultTheModelRelays()
    {
        var orderId = await OrderAsync("shipped");
        var model = new GatedCallModel("Done.", ("cancel_order", "orderId", orderId.ToString()));
        RequestContext.CurrentUserEmail = Customer;
        RequestContext.CurrentUserRole = "customer";
        RequestContext.CurrentSessionId = "";

        var chunks = await StreamAsync(Services(model, new GatedTools()));

        string.Concat(chunks).Should().Be("Done.");
        model.ToolResults.Should().ContainSingle().Which.Should().Contain(Shipped(orderId), "the model still gets the words, as before");
    }

    [Fact]
    public async Task Streaming_UngatedRun_IsUntouched()
    {
        var model = new GatedCallModel("The price is 79.99.", ("lookup", "sku", "XM5"));
        using var scope = AsCustomer();

        var chunks = await StreamAsync(Services(model, new GatedTools()));

        string.Concat(chunks).Should().Be("The price is 79.99.");
    }

    // ───────────────────────── scope hygiene ─────────────────────────

    [Fact]
    public async Task ASentenceIsNotCarriedIntoTheNextRequest()
    {
        var orderId = await OrderAsync("shipped");
        var tools = new GatedTools();
        using var scope = AsCustomer();

        var first = await AgentHost.RunAgentWithHistoryAsync(
            Services(new GatedCallModel("", ("cancel_order", "orderId", orderId.ToString())), tools), "cancel");
        var second = await AgentHost.RunAgentWithHistoryAsync(Services(new GatedCallModel("Hi."), tools), "hello");

        first.Should().Be(Shipped(orderId));
        second.Should().Be("Hi.");
    }

    [Fact]
    public void ForModel_WithoutAnActiveSink_LeavesTheResultForTheModelToRelay()
    {
        var original = new { status = "refused", message = "some sentence" };

        HitlGate.ForModel(original).Should().BeSameAs(original);
    }

    // ───────────────────────── same strings on both stacks ─────────────────────────

    private static JsonElement Vector()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !Directory.Exists(Path.Combine(dir.FullName, "agents", "python")))
        {
            dir = dir.Parent;
        }

        var path = Path.Combine(dir!.FullName, "agents", "python", "config", "policies", "testdata", "gate-sentences.v1.json");
        return JsonDocument.Parse(File.ReadAllText(path)).RootElement.Clone();
    }

    [Fact]
    public void TheUserFacingSentences_MatchTheVectorPythonIsPinnedTo()
    {
        var vector = Vector();

        ParkPrechecks.NotSubmitted.Should().Be(vector.GetProperty("not_submitted").GetString());
        ParkPrechecks.OrderNotFound.Should().Be(vector.GetProperty("order_not_found").GetString());
        ParkPrechecks.ReturnNotFound.Should().Be(vector.GetProperty("return_not_found").GetString());
        ParkPrechecks.ParkFailedReply.Should().Be(vector.GetProperty("park_failed_reply").GetString());

        foreach (var parked in vector.GetProperty("parked").EnumerateArray())
        {
            HitlGate.ParkedReply(
                    parked.GetProperty("tool").GetString()!,
                    parked.GetProperty("input").GetRawText(),
                    Guid.Parse(parked.GetProperty("request_id").GetString()!))
                .Should().Be(parked.GetProperty("sentence").GetString());
        }

        var role = vector.GetProperty("role_refusal");
        ParkPrechecks.RoleRefusal(
                new AgentSettings(),
                role.GetProperty("tool").GetString()!,
                role.GetProperty("declared_roles").EnumerateArray().Select(r => r.GetString()!).ToList(),
                role.GetProperty("caller_role").GetString())
            .Should().Be(role.GetProperty("sentence").GetString());
    }

    // ───────────────────────── the A2A executor ─────────────────────────

    [Fact]
    public async Task A2AExecutor_DeliversTheRefusalAsTheLastPartOfTheAnswer()
    {
        var model = new GatedCallModel("Done, I backordered it.", ("place_backorder", "productId", Guid.NewGuid().ToString()));
        var tools = new GatedTools();
        WebApplication app = AgentHost.Build(
            name: "inventory-fulfillment",
            description: "Gate notice test specialist.",
            port: 8084,
            onMessage: (message, services) => AgentHost.RunAgentWithHistoryAsync(services, message),
            configureServices: (builder, settings) =>
            {
                builder.WebHost.UseTestServer();
                builder.Services.AddSingleton<AIAgent>(sp =>
                    SpecialistPipeline.Apply(
                        model.AsAIAgent(SpecialistAgentFactory.BuildOptions(settings, "You are a test specialist.", "inventory-fulfillment", tools.All())),
                        settings, sp, "inventory-fulfillment"));
            });
        await app.StartAsync();
        try
        {
            using var http = new HttpClient(new HeaderHandler(
                app.GetTestServer().CreateHandler(),
                ScriptedSpecialist.CallerHeaders("alice@example.com", "customer")))
            { BaseAddress = new Uri("http://localhost") };
            var client = A2ASupport.Sdk(http);

            var response = await client.SendMessageAsync(A2ASupport.Request("backorder it"));

            var text = A2ASupport.Text(response.Task!);
            text.Should().Be(
                "Done, I backordered it.\n\nYour account is not permitted to place backorders; that is limited to admin, seller accounts. " +
                ParkPrechecks.NotSubmitted);
            tools.Ran.Should().Be(0);
        }
        finally
        {
            await app.StopAsync();
            await app.DisposeAsync();
        }
    }
}
