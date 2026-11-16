using Dapper;
using ECommerceAgents.Orchestrator.Routes;
using ECommerceAgents.Shared.Approvals;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Orchestration;
using ECommerceAgents.Shared.Policy;
using ECommerceAgents.Shared.Workflows;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;
using Microsoft.Extensions.DependencyInjection;
using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Xunit;
using S = ECommerceAgents.Shared.Approvals.ApprovalStatus;

namespace ECommerceAgents.Orchestrator.Tests;

/// <summary>
/// D-5n — the unified approvals API on .NET: the .NET twin of Python's <c>tests/test_approvals_routes.py</c>. Real Postgres, the real
/// <c>approvals.yaml</c>, the real repository and executor; only the workflow mode is a stub (the checkpoint resume itself is covered by
/// <c>ReturnReplaceCheckpointResumeTests</c>).
/// </summary>
/// <remarks>
/// The invariants asserted here are the ones the Python stack asserts, because both serve the same web UI over the same tables:
/// a reviewer can never be the requester; staff means support or admin and the role must be at or above the route the policy assigned;
/// edits only for the kind's editable fields and never raising the amount; one decision executes once under concurrency; a decision
/// that fails to execute is not recorded and the claim is handed back; the resumed action runs as the original requester with the
/// reviewer recorded separately.
/// </remarks>
[Collection(nameof(LocalPostgresCollection))]
public sealed class ApprovalRoutesTests : IAsyncLifetime
{
    private const string Customer = "cust@example.com";
    private const string Other = "other@example.com";
    private const string Support = "support@example.com";
    private const string Support2 = "support2@example.com";
    private const string Admin = "admin@example.com";

    private readonly PostgresFixture _pg;
    private DatabasePool _pool = null!;
    private AgentSettings _settings = null!;
    private ApprovalRepository _repo = null!;
    private ApprovalIntake _intake = null!;
    private Guid _customerId;
    private Guid _orderId;

    public ApprovalRoutesTests(PostgresFixture pg) => _pg = pg;

    public async Task InitializeAsync()
    {
        _settings = new AgentSettings { DatabaseUrl = _pg.ConnectionString };
        _pool = new DatabasePool(_settings);
        _repo = new ApprovalRepository(_pool, _settings, ApprovalPolicyLocator.Current);
        _intake = new ApprovalIntake(_pool, _repo, ApprovalPolicyLocator.Current);
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            @"TRUNCATE approval_events, approvals, outbox, idempotency_keys, tool_approval_requests, hitl_requests,
                       workflow_checkpoints, agent_execution_steps, usage_logs, messages, conversations, returns,
                       order_status_history, order_items, orders, users
              RESTART IDENTITY CASCADE");
        foreach (var (email, role) in new[] { (Customer, "customer"), (Other, "customer"), (Support, "support"), (Support2, "support"), (Admin, "admin") })
        {
            var id = await conn.ExecuteScalarAsync<Guid>(
                "INSERT INTO users (email, password_hash, name, role) VALUES (@email, 'x', 'T', @role) RETURNING id", new { email, role });
            if (email == Customer) _customerId = id;
        }
        _orderId = await NewOrderAsync(199.99m);
    }

    public async Task DisposeAsync() => await _pool.DisposeAsync();

    // ───────────────────────────── seeding ─────────────────────────────

    private async Task<Guid> NewOrderAsync(decimal total, string status = "placed")
    {
        await using var conn = await _pool.OpenAsync();
        const string addr = "{\"street\":\"1 Test\",\"city\":\"SF\",\"state\":\"CA\",\"zip\":\"94105\",\"country\":\"US\"}";
        return await conn.ExecuteScalarAsync<Guid>(
            "INSERT INTO orders (user_id, status, total, shipping_address) VALUES (@uid, @status, @total, @addr::jsonb) RETURNING id",
            new { uid = _customerId, status, total, addr });
    }

    /// <summary>A gated tool call as the HITL gate leaves it, with its inbox row opened by the intake (the policy routes it).</summary>
    private async Task<(Guid RequestId, Approval Approval)> SeedToolApprovalAsync(
        string tool, object input, string requester = Customer, Guid? conversationId = null)
    {
        await using var conn = await _pool.OpenAsync();
        var requestId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO tool_approval_requests (user_email, session_id, agent_name, tool_name, tool_input)
              VALUES (@requester, @conversationId, 'order-management', @tool, @input::jsonb) RETURNING id",
            new { requester, conversationId, tool, input = JsonSerializer.Serialize(input) });
        using var doc = JsonDocument.Parse(JsonSerializer.Serialize(input));
        var approval = await _intake.OpenToolApprovalAsync(requestId, requester, conversationId?.ToString(), tool, doc.RootElement.Clone());
        return (requestId, approval);
    }

    private async Task<Guid> NewConversationAsync()
    {
        await using var conn = await _pool.OpenAsync();
        return await conn.ExecuteScalarAsync<Guid>(
            "INSERT INTO conversations (user_id, title) VALUES (@uid, 't') RETURNING id", new { uid = _customerId });
    }

    /// <summary>A paused return: usage log, checkpoint, hitl row, and an inbox row at the given route.</summary>
    private async Task<(Guid RunId, Guid HitlId, Approval Approval)> SeedWorkflowApprovalAsync(
        string route = "support", decimal amount = 80m, string requester = Customer, Guid? conversationId = null)
    {
        await using var conn = await _pool.OpenAsync();
        var userId = await conn.ExecuteScalarAsync<Guid>("SELECT id FROM users WHERE email = @requester", new { requester });
        var runId = await conn.ExecuteScalarAsync<Guid>(
            "INSERT INTO usage_logs (user_id, agent_name, input_summary, status) VALUES (@userId, 'orchestrator', 'return', 'success') RETURNING id",
            new { userId });
        var checkpointId = Guid.NewGuid();
        await conn.ExecuteAsync(
            "INSERT INTO workflow_checkpoints (checkpoint_id, workflow_name, payload, session_id, usage_log_id) VALUES (@checkpointId, 'return-replace', '{}'::jsonb, 'sess-1', @runId)",
            new { checkpointId, runId });
        var hitlId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO hitl_requests (workflow_run_id, request_id, checkpoint_id, user_email, kind, payload, status)
              VALUES (@runId, 'req-1', @checkpointId, @requester, 'return_approval', @payload::jsonb, 'pending') RETURNING id",
            new { runId, checkpointId, requester, payload = JsonSerializer.Serialize(new { session_id = "sess-1" }) });
        var approval = await _intake.OpenWorkflowApprovalAsync(
            hitlId, requester, conversationId?.ToString(),
            new RoutedPolicy(route, ["test rule"], new Dictionary<string, object?>(), "test-policy", amount));
        return (runId, hitlId, approval);
    }

    // ───────────────────────────── clients ─────────────────────────────

    private HttpClient ClientFor(string? email, string role = "customer", StubMode? mode = null)
    {
        var server = OrchestratorTestHost.Create(
            _pool,
            r =>
            {
                r.MapApprovalRoutes();
                r.MapHitlRoutes();
                r.MapOrchestrationRoutes();
            },
            configureServices: services =>
            {
                services.AddSingleton<IChatClient>(new FakeChatClient().EnqueueResponse("unused"));
                services.AddSingleton<AIAgent>(sp =>
                    sp.GetRequiredService<IChatClient>().AsAIAgent(instructions: "t", name: "orchestrator"));
                services.AddSingleton<IOrchestrationMode>(mode ?? new StubMode());
            });
        var client = server.CreateClient();
        if (email is not null)
        {
            client.DefaultRequestHeaders.Add("X-Test-Email", email);
            client.DefaultRequestHeaders.Add("X-Test-Role", role);
        }
        return client;
    }

    private HttpClient SupportClient(StubMode? mode = null, string email = Support) => ClientFor(email, "support", mode);
    private HttpClient AdminClient(StubMode? mode = null) => ClientFor(Admin, "admin", mode);
    private HttpClient CustomerClient(string email = Customer) => ClientFor(email, "customer");

    private static Task<HttpResponseMessage> DecideAsync(HttpClient client, Guid id, object body) =>
        client.PostAsJsonAsync($"/api/approvals/{id}/decide", body);

    private static async Task<JsonElement> JsonOf(HttpResponseMessage response) =>
        await response.Content.ReadFromJsonAsync<JsonElement>();

    private async Task<string> StatusAsync(Guid approvalId) => (await _repo.GetAsync(approvalId))!.Status;

    // ───────────────────────────── the queue ─────────────────────────────

    [Fact]
    public async Task TheQueue_RefusesTheAnonymousAndNonStaff()
    {
        using var anonymous = ClientFor(null);
        (await anonymous.GetAsync("/api/approvals")).StatusCode.Should().Be(HttpStatusCode.Unauthorized);

        foreach (var role in new[] { "customer", "seller", "power_user" })
        {
            using var client = ClientFor("someone@example.com", role);
            var response = await client.GetAsync("/api/approvals");
            response.StatusCode.Should().Be(HttpStatusCode.Forbidden, $"{role} is not staff");
            (await JsonOf(response)).GetProperty("detail").GetString().Should().Be("Staff access required");
        }
    }

    [Fact]
    public async Task SupportSeesWhatIsRoutedToSupport_AdminSeesEverything()
    {
        var (_, support) = await SeedToolApprovalAsync("cancel_order", new { order_id = _orderId.ToString() }); // 199.99: support
        var manager = (await SeedWorkflowApprovalAsync("manager")).Approval;
        support.AssignedRole.Should().Be("support");

        using var s = SupportClient();
        var forSupport = await JsonOf(await s.GetAsync("/api/approvals"));
        forSupport.GetProperty("approvals").EnumerateArray().Select(a => a.GetProperty("id").GetString())
            .Should().Contain(support.Id.ToString()).And.NotContain(manager.Id.ToString());

        using var a = AdminClient();
        var forAdmin = await JsonOf(await a.GetAsync("/api/approvals"));
        forAdmin.GetProperty("approvals").EnumerateArray().Select(x => x.GetProperty("id").GetString())
            .Should().Contain([support.Id.ToString(), manager.Id.ToString()]);
        forAdmin.GetProperty("total").GetInt32().Should().Be(2);
    }

    [Fact]
    public async Task TheQueueRowIsASummary_WithoutTheEvidenceBlob()
    {
        await SeedWorkflowApprovalAsync();
        using var admin = AdminClient();
        var row = (await JsonOf(await admin.GetAsync("/api/approvals"))).GetProperty("approvals")[0];

        row.EnumerateObject().Select(p => p.Name).Should().BeEquivalentTo(new[]
        {
            "id", "source", "kind", "requester_email", "amount", "route", "assigned_role", "status", "due_at", "escalation_level",
            "created_at", "updated_at",
        });
        row.GetProperty("kind").GetString().Should().Be("return_approval");
        row.GetProperty("amount").GetDouble().Should().Be(80.0);
        row.GetProperty("status").GetString().Should().Be("pending_review");
    }

    [Fact]
    public async Task TheStatusFilter_TakesOneStatus_All_OrRefusesAnUnknownOne()
    {
        var (_, approval) = await SeedToolApprovalAsync("cancel_order", new { order_id = _orderId.ToString() });
        await _repo.TransitionAsync(approval.Id, [S.PendingReview], S.Rejected, new TransitionOptions { DecidedBy = Admin });
        using var admin = AdminClient();

        (await JsonOf(await admin.GetAsync("/api/approvals"))).GetProperty("total").GetInt32().Should().Be(0, "the default is what is waiting");
        (await JsonOf(await admin.GetAsync("/api/approvals?status=rejected"))).GetProperty("total").GetInt32().Should().Be(1);
        (await JsonOf(await admin.GetAsync("/api/approvals?status=all"))).GetProperty("total").GetInt32().Should().Be(1);
        (await admin.GetAsync("/api/approvals?status=banana")).StatusCode.Should().Be(HttpStatusCode.UnprocessableEntity);
    }

    [Fact]
    public async Task TheDetail_CarriesEventsEvidenceEditableFieldsAndSourceDetail()
    {
        var (requestId, approval) = await SeedToolApprovalAsync("initiate_return", new { order_id = _orderId.ToString(), reason = "wrong size" });
        using var admin = AdminClient();

        var detail = await JsonOf(await admin.GetAsync($"/api/approvals/{approval.Id}"));

        detail.GetProperty("id").GetString().Should().Be(approval.Id.ToString());
        detail.GetProperty("recommendation").GetProperty("reasons").GetArrayLength().Should().BeGreaterThan(0);
        detail.GetProperty("recommendation").GetProperty("policy_version").GetString().Should().NotBeNullOrEmpty();
        detail.GetProperty("editable_fields").EnumerateArray().Select(e => e.GetString()).Should().Equal("refund_method");
        detail.GetProperty("events").EnumerateArray().Select(e => e.GetProperty("action").GetString()).Should().Equal("created", "routed");
        var source = detail.GetProperty("source_detail");
        source.GetProperty("tool_name").GetString().Should().Be("initiate_return");
        source.GetProperty("tool_input").GetProperty("reason").GetString().Should().Be("wrong size");
        _ = requestId;
    }

    [Fact]
    public async Task TheDetail_IsRefusedForSupportWhenRoutedToAManager_AndTheIdIsValidated()
    {
        var manager = (await SeedWorkflowApprovalAsync("manager")).Approval;
        using var support = SupportClient();

        var refused = await support.GetAsync($"/api/approvals/{manager.Id}");
        refused.StatusCode.Should().Be(HttpStatusCode.Forbidden);
        (await JsonOf(refused)).GetProperty("detail").GetString().Should().Be("This approval is routed to a manager");

        (await support.GetAsync($"/api/approvals/{Guid.NewGuid()}")).StatusCode.Should().Be(HttpStatusCode.NotFound);
        (await support.GetAsync("/api/approvals/not-a-guid")).StatusCode.Should().Be(HttpStatusCode.NotFound);
    }

    [Fact]
    public async Task MyApprovals_IsStatusOnly_AndOnlyTheCallersOwn()
    {
        await SeedWorkflowApprovalAsync(requester: Customer);
        using var customer = CustomerClient();
        var mine = (await JsonOf(await customer.GetAsync("/api/me/approvals"))).GetProperty("approvals");

        mine.GetArrayLength().Should().Be(1);
        mine[0].EnumerateObject().Select(p => p.Name).Should().BeEquivalentTo(new[] { "id", "kind", "status", "amount", "created_at", "updated_at" });

        using var other = CustomerClient(Other);
        (await JsonOf(await other.GetAsync("/api/me/approvals"))).GetProperty("approvals").GetArrayLength().Should().Be(0);
        using var anonymous = ClientFor(null);
        (await anonymous.GetAsync("/api/me/approvals")).StatusCode.Should().Be(HttpStatusCode.Unauthorized);
    }

    // ───────────────────────────── who may decide ─────────────────────────────

    [Fact]
    public async Task ACustomer_CannotDecide_EvenTheirOwn()
    {
        var (_, approval) = await SeedToolApprovalAsync("cancel_order", new { order_id = _orderId.ToString() });
        using var customer = CustomerClient();

        var response = await DecideAsync(customer, approval.Id, new { decision = "approve" });

        response.StatusCode.Should().Be(HttpStatusCode.Forbidden);
        (await JsonOf(response)).GetProperty("detail").GetString().Should().Be("Staff access required");
        (await StatusAsync(approval.Id)).Should().Be(S.PendingReview);
    }

    /// <summary>The reviewer can never be the requester — even staff, even an admin, however the case is written.</summary>
    [Theory]
    [InlineData("support", "support@example.com")]
    [InlineData("support", "SUPPORT@example.com")]
    [InlineData("admin", "admin@example.com")]
    public async Task AReviewerCanNeverBeTheRequester(string role, string requester)
    {
        var (_, approval) = await SeedToolApprovalAsync(
            "initiate_return", new { order_id = _orderId.ToString(), reason = "x" }, requester: requester);
        // The route may be support or manager; make it decidable by this role so only the self-check can refuse.
        await using (var conn = await _pool.OpenAsync())
        {
            await conn.ExecuteAsync("UPDATE approvals SET assigned_role = 'support' WHERE id = @id", new { id = approval.Id });
        }
        using var client = ClientFor(requester.ToLowerInvariant(), role);

        var response = await DecideAsync(client, approval.Id, new { decision = "approve" });

        response.StatusCode.Should().Be(HttpStatusCode.Forbidden);
        (await JsonOf(response)).GetProperty("detail").GetString().Should().Be("You cannot approve your own request");
        (await StatusAsync(approval.Id)).Should().Be(S.PendingReview);
        await using var check = await _pool.OpenAsync();
        (await check.ExecuteScalarAsync<long>("SELECT count(*) FROM returns")).Should().Be(0, "the refused decision must not have executed anything");
    }

    [Fact]
    public async Task SupportCannotDecideAManagerRoute_ButAdminCanDecideASupportRoute()
    {
        var manager = (await SeedWorkflowApprovalAsync("manager")).Approval;
        using var support = SupportClient();
        var refused = await DecideAsync(support, manager.Id, new { decision = "reject" });
        refused.StatusCode.Should().Be(HttpStatusCode.Forbidden);
        (await JsonOf(refused)).GetProperty("detail").GetString().Should().Be("This approval is routed to a manager");
        (await StatusAsync(manager.Id)).Should().Be(S.PendingReview);

        var (_, supportRouted) = await SeedToolApprovalAsync("cancel_order", new { order_id = _orderId.ToString() });
        using var admin = AdminClient();
        (await DecideAsync(admin, supportRouted.Id, new { decision = "approve" })).EnsureSuccessStatusCode();
    }

    [Theory]
    [InlineData("""{"decision":"maybe"}""")]
    [InlineData("""{}""")]
    public async Task ABadDecision_Is422_BeforeAnythingIsTouched(string body)
    {
        var (_, approval) = await SeedToolApprovalAsync("cancel_order", new { order_id = _orderId.ToString() });
        using var admin = AdminClient();
        var response = await admin.PostAsync(
            $"/api/approvals/{approval.Id}/decide", new StringContent(body, System.Text.Encoding.UTF8, "application/json"));
        response.StatusCode.Should().Be(HttpStatusCode.UnprocessableEntity);
        (await StatusAsync(approval.Id)).Should().Be(S.PendingReview);
    }

    // ───────────────────────────── tool approvals ─────────────────────────────

    [Fact]
    public async Task ApprovingAToolRequest_ExecutesItAsTheRequester_AndRecordsTheReviewerSeparately()
    {
        var (requestId, approval) = await SeedToolApprovalAsync("cancel_order", new { order_id = _orderId.ToString(), reason = "x" });
        using var admin = AdminClient();

        var response = await DecideAsync(admin, approval.Id, new { decision = "approve", note = "fine" });
        response.EnsureSuccessStatusCode();
        var body = await JsonOf(response);

        body.GetProperty("result").GetProperty("success").GetBoolean().Should().BeTrue();
        var final = body.GetProperty("approval");
        final.GetProperty("status").GetString().Should().Be(S.Executed);
        final.GetProperty("decided_by").GetString().Should().Be(Admin);
        final.GetProperty("requester_email").GetString().Should().Be(Customer);

        await using var conn = await _pool.OpenAsync();
        (await conn.ExecuteScalarAsync<string>("SELECT status FROM orders WHERE id = @id", new { id = _orderId })).Should().Be("cancelled");
        var legacy = await conn.QueryFirstAsync("SELECT status, approved_by, admin_note FROM tool_approval_requests WHERE id = @requestId", new { requestId });
        ((string)legacy.status).Should().Be("executed");
        ((string)legacy.approved_by).Should().Be(Admin);
        ((string)legacy.admin_note).Should().Be("fine");

        var events = (await _repo.ListEventsAsync(approval.Id)).Select(e => e.Action).ToList();
        events.Should().Equal("created", "routed", "decided", "executed");
    }

    [Fact]
    public async Task AnApprovedActionThatFails_IsRecordedAsFailed_NotExecuted()
    {
        var (_, approval) = await SeedToolApprovalAsync("cancel_order", new { order_id = Guid.NewGuid().ToString() });
        using var admin = AdminClient();

        var body = await JsonOf(await DecideAsync(admin, approval.Id, new { decision = "approve" }));

        body.GetProperty("result").GetProperty("success").GetBoolean().Should().BeFalse();
        body.GetProperty("approval").GetProperty("status").GetString().Should().Be(S.Failed);
    }

    /// <summary>The test the claim exists for: two reviewers click Approve; the action runs once.</summary>
    [Fact]
    public async Task TwoConcurrentApprovals_ExecuteTheActionExactlyOnce()
    {
        var (_, approval) = await SeedToolApprovalAsync("initiate_return", new { order_id = _orderId.ToString(), reason = "double click" });
        await using (var conn = await _pool.OpenAsync())
        {
            await conn.ExecuteAsync("UPDATE orders SET status = 'delivered' WHERE id = @id", new { id = _orderId });
            // Whatever the policy chose for this order, make it a support route so both reviewers are allowed to race.
            await conn.ExecuteAsync("UPDATE approvals SET assigned_role = 'support' WHERE id = @id", new { id = approval.Id });
        }
        using var a = AdminClient();
        using var b = SupportClient();

        var responses = await Task.WhenAll(
            DecideAsync(a, approval.Id, new { decision = "approve" }),
            DecideAsync(b, approval.Id, new { decision = "approve" }));

        responses.Count(r => r.IsSuccessStatusCode).Should().Be(1, "only one decision reaches the executor");
        responses.Single(r => !r.IsSuccessStatusCode).StatusCode.Should().Be(HttpStatusCode.Conflict);
        await using var check = await _pool.OpenAsync();
        (await check.ExecuteScalarAsync<long>("SELECT count(*) FROM returns WHERE order_id = @id", new { id = _orderId }))
            .Should().Be(1, "the side effect must run exactly once, not merely be reported once");
        (await StatusAsync(approval.Id)).Should().Be(S.Executed);
    }

    [Fact]
    public async Task ADecisionThatCannotBeRecorded_HandsTheClaimBack_AndLeavesTheApprovalPending()
    {
        var (requestId, approval) = await SeedToolApprovalAsync("cancel_order", new { order_id = _orderId.ToString() });
        // Someone closed the legacy record out from under the approval.
        await using (var conn = await _pool.OpenAsync())
        {
            await conn.ExecuteAsync("UPDATE tool_approval_requests SET status = 'denied' WHERE id = @requestId", new { requestId });
        }
        using var admin = AdminClient();

        var response = await DecideAsync(admin, approval.Id, new { decision = "approve" });

        response.StatusCode.Should().Be(HttpStatusCode.Conflict);
        var after = (await _repo.GetAsync(approval.Id))!;
        after.Status.Should().Be(S.PendingReview, "a decision that fails is not recorded");
        after.ClaimedBy.Should().BeNull("the claim is released so a reviewer can try again");
    }

    [Fact]
    public async Task AnApprovalBeingDecidedElsewhere_Is409_AndTheOtherHoldersClaimIsUntouched()
    {
        var (_, approval) = await SeedToolApprovalAsync("cancel_order", new { order_id = _orderId.ToString() });
        await _repo.ClaimAsync(approval.Id, "someone-else", [S.PendingReview], 600);
        using var admin = AdminClient();

        var response = await DecideAsync(admin, approval.Id, new { decision = "approve" });

        response.StatusCode.Should().Be(HttpStatusCode.Conflict);
        (await JsonOf(response)).GetProperty("detail").GetString().Should().Be("Request is being decided by someone else");
        (await _repo.GetAsync(approval.Id))!.ClaimedBy.Should().Be("someone-else");
    }

    [Fact]
    public async Task ADecidedApproval_CannotBeDecidedAgain()
    {
        var (_, approval) = await SeedToolApprovalAsync("cancel_order", new { order_id = _orderId.ToString() });
        using var admin = AdminClient();
        (await DecideAsync(admin, approval.Id, new { decision = "reject", note = "no" })).EnsureSuccessStatusCode();

        var again = await DecideAsync(admin, approval.Id, new { decision = "approve" });

        again.StatusCode.Should().Be(HttpStatusCode.Conflict);
        (await JsonOf(again)).GetProperty("detail").GetString().Should().Be("Request is already rejected");
    }

    [Fact]
    public async Task RejectingAToolRequest_ExecutesNothing_AndDeniesTheLegacyRecord()
    {
        var (requestId, approval) = await SeedToolApprovalAsync("cancel_order", new { order_id = _orderId.ToString() });
        using var admin = AdminClient();

        var body = await JsonOf(await DecideAsync(admin, approval.Id, new { decision = "reject", note = "no" }));

        body.GetProperty("approval").GetProperty("status").GetString().Should().Be(S.Rejected);
        body.GetProperty("result").GetProperty("status").GetString().Should().Be("denied");
        await using var conn = await _pool.OpenAsync();
        (await conn.ExecuteScalarAsync<string>("SELECT status FROM orders WHERE id = @id", new { id = _orderId })).Should().Be("placed");
        (await conn.ExecuteScalarAsync<string>("SELECT status FROM tool_approval_requests WHERE id = @requestId", new { requestId })).Should().Be("denied");
    }

    // ───────────────────────────── edits ─────────────────────────────

    [Fact]
    public async Task AnEditToAFieldOutsideTheKindsAllowlist_Is422_AndNothingRuns()
    {
        var (_, approval) = await SeedToolApprovalAsync("initiate_return", new { order_id = _orderId.ToString(), reason = "x" });
        using var admin = AdminClient();

        var response = await DecideAsync(admin, approval.Id, new
        {
            decision = "approve",
            edits = new { order_id = Guid.NewGuid().ToString() }, // would point the approval at someone else's order
        });

        response.StatusCode.Should().Be(HttpStatusCode.UnprocessableEntity);
        (await JsonOf(response)).GetProperty("detail").GetString().Should().Contain("order_id");
        (await StatusAsync(approval.Id)).Should().Be(S.PendingReview);
        (await _repo.GetAsync(approval.Id))!.ClaimedBy.Should().BeNull("validation happens before anything is claimed");
    }

    [Fact]
    public async Task AKindWithNoEditableFields_TakesNoEdits()
    {
        var (_, approval) = await SeedToolApprovalAsync("cancel_order", new { order_id = _orderId.ToString() });
        using var admin = AdminClient();
        var response = await DecideAsync(admin, approval.Id, new { decision = "approve", edits = new { reason = "changed" } });
        response.StatusCode.Should().Be(HttpStatusCode.UnprocessableEntity);
    }

    [Fact]
    public async Task AnEditableField_IsMerged_AndTheApprovalIsRecordedAsApprovedWithEdits()
    {
        await using (var conn = await _pool.OpenAsync())
        {
            await conn.ExecuteAsync("UPDATE orders SET status = 'delivered' WHERE id = @id", new { id = _orderId });
        }
        var (_, approval) = await SeedToolApprovalAsync("initiate_return", new { order_id = _orderId.ToString(), reason = "x" });
        using var admin = AdminClient();

        var body = await JsonOf(await DecideAsync(admin, approval.Id, new { decision = "approve", edits = new { refund_method = "store_credit" } }));

        body.GetProperty("result").GetProperty("success").GetBoolean().Should().BeTrue();
        await using var check = await _pool.OpenAsync();
        (await check.ExecuteScalarAsync<string>("SELECT refund_method FROM returns WHERE order_id = @id", new { id = _orderId })).Should().Be("store_credit");
        var decision = (await _repo.GetAsync(approval.Id))!.Decision!.Value;
        decision.GetProperty("edits").GetProperty("refund_method").GetString().Should().Be("store_credit");
        (await _repo.ListEventsAsync(approval.Id)).Single(e => e.Action == "decided")
            .Payload.GetProperty("to").GetString().Should().Be(S.ApprovedWithEdits);
    }

    [Fact]
    public async Task ABadRefundMethodEdit_Is422()
    {
        var (_, approval) = await SeedToolApprovalAsync("initiate_return", new { order_id = _orderId.ToString(), reason = "x" });
        using var admin = AdminClient();
        var response = await DecideAsync(admin, approval.Id, new { decision = "approve", edits = new { refund_method = "bitcoin" } });
        response.StatusCode.Should().Be(HttpStatusCode.UnprocessableEntity);
    }

    [Fact]
    public async Task ARejectionOrARequestForInfo_TakesNoEdits()
    {
        var (_, approval) = await SeedToolApprovalAsync("initiate_return", new { order_id = _orderId.ToString(), reason = "x" });
        using var admin = AdminClient();
        (await DecideAsync(admin, approval.Id, new { decision = "reject", edits = new { refund_method = "store_credit" } }))
            .StatusCode.Should().Be(HttpStatusCode.UnprocessableEntity);
        (await DecideAsync(admin, approval.Id, new { decision = "request_info", note = "why?", edits = new { refund_method = "store_credit" } }))
            .StatusCode.Should().Be(HttpStatusCode.UnprocessableEntity);
        (await StatusAsync(approval.Id)).Should().Be(S.PendingReview);
    }

    // ───────────────────────────── workflow approvals ─────────────────────────────

    [Fact]
    public async Task ApprovingAPausedReturn_ResumesItAsTheRequester_WithTheReviewersEditsAndTheReviewerRecordedSeparately()
    {
        var (runId, hitlId, approval) = await SeedWorkflowApprovalAsync(amount: 80m);
        var mode = new StubMode();
        using var support = SupportClient(mode);

        var response = await DecideAsync(support, approval.Id, new
        {
            decision = "approve",
            edits = new { amount = 25.5, resolution = "replacement" },
            note = "partial",
        });
        response.EnsureSuccessStatusCode();
        var body = await JsonOf(response);

        mode.Calls.Should().Be(1);
        mode.SeenEmail.Should().Be(Customer, "the resumed workflow runs as the requester so their tier and ownership checks apply");
        mode.SeenRole.Should().Be("customer");
        mode.LastDecision!.Decision.Should().Be("approve");
        mode.LastDecision.Amount.Should().Be(25.5m);
        mode.LastDecision.Resolution.Should().Be("replacement");
        mode.LastDecision.DecidedBy.Should().Be(Support, "the reviewer travels in the decision, not the identity");
        mode.LastDecision.ApprovalId.Should().Be(approval.Id.ToString());
        mode.LastDecision.Note.Should().Be("partial");

        var final = body.GetProperty("approval");
        final.GetProperty("status").GetString().Should().Be(S.Executed);
        final.GetProperty("decided_by").GetString().Should().Be(Support);
        body.GetProperty("result").GetProperty("text").GetString().Should().Be("Return approved.");

        await using var conn = await _pool.OpenAsync();
        var hitl = await conn.QueryFirstAsync("SELECT status, response::text AS response FROM hitl_requests WHERE id = @hitlId", new { hitlId });
        ((string)hitl.status).Should().Be("approved");
        ((string)hitl.response).Should().Contain(Support);
        _ = runId;
    }

    [Fact]
    public async Task AWorkflowThatDoesNotRunToItsLastStep_IsRecordedAsFailed()
    {
        var (_, _, approval) = await SeedWorkflowApprovalAsync();
        using var admin = AdminClient(new StubMode { Completed = false });
        var body = await JsonOf(await DecideAsync(admin, approval.Id, new { decision = "approve" }));
        body.GetProperty("approval").GetProperty("status").GetString().Should().Be(S.Failed);
    }

    [Fact]
    public async Task RejectingAPausedReturn_ResumesItAsARejection_CreatingNothing()
    {
        var (_, hitlId, approval) = await SeedWorkflowApprovalAsync();
        var mode = new StubMode();
        using var support = SupportClient(mode);

        var body = await JsonOf(await DecideAsync(support, approval.Id, new { decision = "reject", note = "not eligible" }));

        mode.LastDecision!.Decision.Should().Be("reject");
        body.GetProperty("approval").GetProperty("status").GetString().Should().Be(S.Rejected);
        await using var conn = await _pool.OpenAsync();
        (await conn.ExecuteScalarAsync<string>("SELECT status FROM hitl_requests WHERE id = @hitlId", new { hitlId })).Should().Be("rejected");
    }

    /// <summary>Edits can narrow what is refunded, never widen it, and only the fields the policy lists.</summary>
    [Theory]
    [InlineData("""{"amount": 80.01}""", "cannot exceed")]
    [InlineData("""{"amount": 1000}""", "cannot exceed")]
    [InlineData("""{"amount": -1}""", "non-negative")]
    [InlineData("""{"amount": "10"}""", "non-negative")]
    [InlineData("""{"amount": true}""", "non-negative")]
    [InlineData("""{"resolution": "cash"}""", "resolution must be one of")]
    [InlineData("""{"note": "x"}""", "Not editable: note")]
    [InlineData("""{"order_id": "x", "amount": 5}""", "Not editable: order_id")]
    public async Task AnInvalidWorkflowEdit_Is422_BeforeTheWorkflowIsTouched(string edits, string detail)
    {
        var (_, hitlId, approval) = await SeedWorkflowApprovalAsync(amount: 80m);
        var mode = new StubMode();
        using var admin = AdminClient(mode);
        var content = new StringContent($$"""{"decision":"approve","edits":{{edits}}}""", System.Text.Encoding.UTF8, "application/json");

        var response = await admin.PostAsync($"/api/approvals/{approval.Id}/decide", content);

        response.StatusCode.Should().Be(HttpStatusCode.UnprocessableEntity);
        (await JsonOf(response)).GetProperty("detail").GetString().Should().Contain(detail);
        mode.Calls.Should().Be(0);
        (await StatusAsync(approval.Id)).Should().Be(S.PendingReview);
        await using var conn = await _pool.OpenAsync();
        (await conn.ExecuteScalarAsync<string>("SELECT status FROM hitl_requests WHERE id = @hitlId", new { hitlId })).Should().Be("pending");
    }

    [Fact]
    public async Task AnEditEqualToTheAmountUnderReview_IsAccepted()
    {
        var (_, _, approval) = await SeedWorkflowApprovalAsync(amount: 80m);
        var mode = new StubMode();
        using var admin = AdminClient(mode);
        (await DecideAsync(admin, approval.Id, new { decision = "approve", edits = new { amount = 80 } })).EnsureSuccessStatusCode();
        mode.LastDecision!.Amount.Should().Be(80m);
    }

    [Fact]
    public async Task AResumeThatFails_IsNotRecorded_AndBothClaimsAreHandedBack()
    {
        var (_, hitlId, approval) = await SeedWorkflowApprovalAsync();
        using var admin = AdminClient(new StubMode { FailWith = new InvalidOperationException("boom") });

        var response = await DecideAsync(admin, approval.Id, new { decision = "approve" });

        response.StatusCode.Should().Be(HttpStatusCode.InternalServerError);
        var after = (await _repo.GetAsync(approval.Id))!;
        after.Status.Should().Be(S.PendingReview);
        after.ClaimedBy.Should().BeNull();
        await using var conn = await _pool.OpenAsync();
        var hitl = await conn.QueryFirstAsync("SELECT status, claimed_at FROM hitl_requests WHERE id = @hitlId", new { hitlId });
        ((string)hitl.status).Should().Be("pending");
        ((DateTime?)hitl.claimed_at).Should().BeNull();

        // And the reviewer can simply try again.
        using var retry = AdminClient(new StubMode());
        (await DecideAsync(retry, approval.Id, new { decision = "approve" })).EnsureSuccessStatusCode();
    }

    [Fact]
    public async Task TwoConcurrentWorkflowApprovals_ResumeTheWorkflowExactlyOnce()
    {
        var (_, _, approval) = await SeedWorkflowApprovalAsync();
        var mode = new StubMode { Delay = TimeSpan.FromMilliseconds(150) };
        using var a = AdminClient(mode);
        using var b = SupportClient(mode);

        var responses = await Task.WhenAll(
            DecideAsync(a, approval.Id, new { decision = "approve" }),
            DecideAsync(b, approval.Id, new { decision = "approve" }));

        responses.Count(r => r.IsSuccessStatusCode).Should().Be(1);
        mode.Calls.Should().Be(1, "the refund side effect must run exactly once");
    }

    // ───────────────────────────── a merchant proposal is not the staff inbox's to decide ─────────────────────────────

    /// <summary>The approval Python's merchant-proposal generator opens: system requester, the seller as subject, in review.</summary>
    private async Task<Approval> SeedProposalApprovalAsync() =>
        await _repo.CreateAsync(new NewApproval(
            Source: "proposal", Kind: "merchant_proposal", RequesterEmail: "merchant-ops@system", SourceRef: Guid.NewGuid(),
            SubjectEmail: "seller@example.com", Status: S.PendingReview, ActorEmail: "merchant-ops@system", ActorRole: "system"));

    [Theory]
    [InlineData("approve", "admin")]
    [InlineData("reject", "admin")]
    [InlineData("request_info", "admin")]
    [InlineData("approve", "support")]
    [InlineData("reject", "support")]
    [InlineData("request_info", "support")]
    public async Task StaffCannotDecideAProposalApproval_WithAnyDecision_AndNothingChanges(string decision, string role)
    {
        var approval = await SeedProposalApprovalAsync();
        using var client = ClientFor($"{role}-reviewer@example.com", role);

        var response = await DecideAsync(client, approval.Id, new { decision, note = "why?" });

        response.StatusCode.Should().Be(HttpStatusCode.UnprocessableEntity);
        (await StatusAsync(approval.Id)).Should().Be("pending_review");
        (await _repo.ListEventsAsync(approval.Id)).Select(e => e.Action).Should().Equal("created");
        (await _repo.GetAsync(approval.Id))!.ClaimedBy.Should().BeNull();
    }

    [Fact]
    public async Task TheAnswerInfoRoute_CannotTouchAProposal()
    {
        var approval = await SeedProposalApprovalAsync();

        foreach (var email in new[] { "seller@example.com", "merchant-ops@system", Admin })
        {
            using var client = ClientFor(email, email == Admin ? "admin" : "seller");
            var response = await client.PostAsJsonAsync($"/api/approvals/{approval.Id}/info", new { message = "here" });
            response.StatusCode.Should().Be(HttpStatusCode.UnprocessableEntity, email);
        }
        (await StatusAsync(approval.Id)).Should().Be("pending_review");
    }

    [Fact]
    public async Task TheStaffList_AndTheStaffDetail_LeaveAProposalOut()
    {
        var approval = await SeedProposalApprovalAsync();
        var tool = (await SeedToolApprovalAsync("cancel_order", new { order_id = _orderId.ToString() })).Approval;
        using var admin = AdminClient();

        var listing = await JsonOf(await admin.GetAsync("/api/approvals?status=all"));
        var detail = await admin.GetAsync($"/api/approvals/{approval.Id}");

        listing.GetProperty("approvals").EnumerateArray().Select(a => a.GetProperty("id").GetString())
            .Should().Equal(tool.Id.ToString());
        detail.StatusCode.Should().Be(HttpStatusCode.NotFound);
        (await JsonOf(detail)).GetProperty("detail").GetString().Should().Be("Approval not found");
    }

    [Fact]
    public async Task TheLegacyResumeAndAdminRoutes_CannotReachAProposal()
    {
        var approval = await SeedProposalApprovalAsync();
        using var admin = AdminClient();

        foreach (var id in new[] { approval.Id, approval.SourceRef!.Value })
        {
            (await admin.PostAsJsonAsync($"/api/orchestration/{id}/resume", new { approved = true })).StatusCode.Should().Be(HttpStatusCode.NotFound);
            (await admin.PostAsJsonAsync($"/api/admin/hitl/requests/{id}/approve", new { })).StatusCode.Should().Be(HttpStatusCode.NotFound);
            (await admin.PostAsJsonAsync($"/api/admin/hitl/requests/{id}/deny", new { })).StatusCode.Should().Be(HttpStatusCode.NotFound);
        }
        (await StatusAsync(approval.Id)).Should().Be("pending_review");
    }

    // ───────────────────────────── request more information ─────────────────────────────

    [Fact]
    public async Task RequestInfo_RoundTrip_ParksTheRunAsksTheCustomerAndReturnsToReviewOnTheAnswer()
    {
        var conversation = await NewConversationAsync();
        var (_, hitlId, approval) = await SeedWorkflowApprovalAsync(conversationId: conversation);
        using var support = SupportClient();

        // The note is required, and the reviewer asks.
        (await DecideAsync(support, approval.Id, new { decision = "request_info" })).StatusCode.Should().Be(HttpStatusCode.UnprocessableEntity);
        var asked = await JsonOf(await DecideAsync(support, approval.Id, new { decision = "request_info", note = "Is the box damaged?" }));
        asked.GetProperty("approval").GetProperty("status").GetString().Should().Be(S.NeedsInfo);
        asked.GetProperty("result").GetProperty("status").GetString().Should().Be("needs_info");

        await using var conn = await _pool.OpenAsync();
        (await conn.ExecuteScalarAsync<string>("SELECT status FROM hitl_requests WHERE id = @hitlId", new { hitlId }))
            .Should().Be("needs_info", "a resume cannot be claimed while the customer is being asked");
        var question = await conn.QueryFirstAsync("SELECT role, content FROM messages WHERE conversation_id = @conversation", new { conversation });
        ((string)question.role).Should().Be("assistant");
        ((string)question.content).Should().Contain("Is the box damaged?");

        // Nobody can approve while it waits on the customer.
        var blocked = await DecideAsync(support, approval.Id, new { decision = "approve" });
        blocked.StatusCode.Should().Be(HttpStatusCode.Conflict);
        (await JsonOf(blocked)).GetProperty("detail").GetString().Should().Be("Waiting for the requester's answer");

        // Only the requester answers.
        using var stranger = CustomerClient(Other);
        (await stranger.PostAsJsonAsync($"/api/approvals/{approval.Id}/info", new { message = "yes" })).StatusCode.Should().Be(HttpStatusCode.Forbidden);
        using var customer = CustomerClient();
        (await customer.PostAsJsonAsync($"/api/approvals/{approval.Id}/info", new { message = "  " })).StatusCode.Should().Be(HttpStatusCode.UnprocessableEntity);
        var answered = await JsonOf(await customer.PostAsJsonAsync($"/api/approvals/{approval.Id}/info", new { message = "Yes, the box is dented." }));
        answered.GetProperty("approval").GetProperty("status").GetString().Should().Be(S.PendingReview);

        (await conn.ExecuteScalarAsync<string>("SELECT status FROM hitl_requests WHERE id = @hitlId", new { hitlId })).Should().Be("pending");
        (await conn.QueryAsync<string>("SELECT role FROM messages WHERE conversation_id = @conversation ORDER BY created_at", new { conversation }))
            .Should().Equal("assistant", "user");
        (await _repo.ListEventsAsync(approval.Id)).Select(e => e.Action)
            .Should().Equal("created", "routed", "info_requested", "info_provided");

        // A second answer is refused; and the reviewer can now approve.
        (await customer.PostAsJsonAsync($"/api/approvals/{approval.Id}/info", new { message = "again" })).StatusCode.Should().Be(HttpStatusCode.Conflict);
        using var admin = AdminClient();
        (await DecideAsync(admin, approval.Id, new { decision = "approve" })).EnsureSuccessStatusCode();
    }

    [Fact]
    public async Task ARejectionIsStillPossibleWhileWaitingOnTheCustomer()
    {
        var (_, hitlId, approval) = await SeedWorkflowApprovalAsync();
        var mode = new StubMode();
        using var support = SupportClient(mode);
        (await DecideAsync(support, approval.Id, new { decision = "request_info", note = "more?" })).EnsureSuccessStatusCode();

        var rejected = await DecideAsync(support, approval.Id, new { decision = "reject" });

        rejected.EnsureSuccessStatusCode();
        (await StatusAsync(approval.Id)).Should().Be(S.Rejected);
        mode.Calls.Should().Be(1, "a reject resumes the parked run as a rejection");
        await using var conn = await _pool.OpenAsync();
        (await conn.ExecuteScalarAsync<string>("SELECT status FROM hitl_requests WHERE id = @hitlId", new { hitlId })).Should().Be("rejected");
    }

    [Fact]
    public async Task RequestInfoOnAToolApproval_WorksToo_AndWritesTheBreachTimerForTheWait()
    {
        var (_, approval) = await SeedToolApprovalAsync("cancel_order", new { order_id = _orderId.ToString() });
        using var support = SupportClient();
        (await DecideAsync(support, approval.Id, new { decision = "request_info", note = "which order?" })).EnsureSuccessStatusCode();

        (await StatusAsync(approval.Id)).Should().Be(S.NeedsInfo);
        await using var conn = await _pool.OpenAsync();
        (await conn.ExecuteScalarAsync<long>("SELECT count(*) FROM outbox WHERE aggregate_id = @id AND topic = 'approval.sla_breached'", new { id = approval.Id }))
            .Should().Be(2, "the review's breach timer plus the one for the wait");
    }

    // ───────────────────────────── the legacy routes are shims ─────────────────────────────

    [Fact]
    public async Task TheLegacyResumeRoute_IsStaffBoundedByThePolicyRoute()
    {
        var (runId, _, approval) = await SeedWorkflowApprovalAsync(route: "manager");
        var mode = new StubMode();
        using var support = SupportClient(mode);
        var refused = await support.PostAsJsonAsync($"/api/orchestration/{runId}/resume", new { approved = true });
        refused.StatusCode.Should().Be(HttpStatusCode.Forbidden, "support cannot decide a manager route, here or on the unified API");
        mode.Calls.Should().Be(0);

        using var admin = AdminClient(mode);
        (await admin.PostAsJsonAsync($"/api/orchestration/{runId}/resume", new { approved = true })).EnsureSuccessStatusCode();
        (await StatusAsync(approval.Id)).Should().Be(S.Executed, "the shim went through the same decide path");
    }

    [Fact]
    public async Task TheLegacyResumeRoute_LetsSupportDecideASupportRoute()
    {
        var (runId, _, approval) = await SeedWorkflowApprovalAsync(route: "support");
        var mode = new StubMode();
        using var support = SupportClient(mode);

        var response = await support.PostAsJsonAsync($"/api/orchestration/{runId}/resume", new { approved = false });

        response.EnsureSuccessStatusCode();
        (await JsonOf(response)).GetProperty("approved").GetBoolean().Should().BeFalse();
        (await StatusAsync(approval.Id)).Should().Be(S.Rejected);
        (await _repo.GetAsync(approval.Id))!.DecidedBy.Should().Be(Support);
    }

    [Fact]
    public async Task TheLegacyAdminApproveAndDeny_AreStaffBoundedByTheRoute_AndGoThroughTheInbox()
    {
        var (requestId, approval) = await SeedToolApprovalAsync("cancel_order", new { order_id = _orderId.ToString() }); // support route
        using var support = SupportClient();

        var approved = await support.PostAsJsonAsync($"/api/admin/hitl/requests/{requestId}/approve", new { note = "ok" });
        approved.EnsureSuccessStatusCode();
        var body = await JsonOf(approved);
        body.GetProperty("status").GetString().Should().Be("approved");
        body.GetProperty("execution_result").GetProperty("success").GetBoolean().Should().BeTrue();
        (await StatusAsync(approval.Id)).Should().Be(S.Executed);

        var manager = await NewOrderAsync(1500m); // over 1000: a manager route
        var (managerRequest, managerApproval) = await SeedToolApprovalAsync("cancel_order", new { order_id = manager.ToString() });
        managerApproval.AssignedRole.Should().Be("admin");
        (await support.PostAsJsonAsync($"/api/admin/hitl/requests/{managerRequest}/deny", new { })).StatusCode
            .Should().Be(HttpStatusCode.Forbidden);
        using var admin = AdminClient();
        (await JsonOf(await admin.PostAsJsonAsync($"/api/admin/hitl/requests/{managerRequest}/deny", new { })))
            .GetProperty("status").GetString().Should().Be("denied");
        (await StatusAsync(managerApproval.Id)).Should().Be(S.Rejected);
    }

    [Fact]
    public async Task ALegacyRequestThatPredatesTheInbox_GetsItsApprovalOnFirstDecision()
    {
        await using var conn = await _pool.OpenAsync();
        var requestId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO tool_approval_requests (user_email, agent_name, tool_name, tool_input)
              VALUES (@Customer, 'order-management', 'cancel_order', @input::jsonb) RETURNING id",
            new { Customer, input = JsonSerializer.Serialize(new { order_id = _orderId.ToString() }) });
        using var admin = AdminClient();

        (await admin.PostAsJsonAsync($"/api/admin/hitl/requests/{requestId}/approve", new { })).EnsureSuccessStatusCode();

        var linked = await conn.ExecuteScalarAsync<Guid?>("SELECT approval_id FROM tool_approval_requests WHERE id = @requestId", new { requestId });
        linked.Should().NotBeNull();
        (await StatusAsync(linked!.Value)).Should().Be(S.Executed);
    }

    // ───────────────────────────── review fixes (W3, S1, S2, S7) ─────────────────────────────

    /// <summary>
    /// Runs <paramref name="body"/> with a trigger that escalates the approval (support to manager) in the same statement that takes the
    /// claim: an escalation that lands between the decider check and the claim, which no request-level test can interleave by hand.
    /// </summary>
    private async Task WithEscalationOnClaimAsync(Func<Task> body)
    {
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            @"CREATE OR REPLACE FUNCTION test_escalate_on_claim() RETURNS trigger AS $$
              BEGIN
                IF OLD.claimed_by IS NULL AND NEW.claimed_by IS NOT NULL THEN
                  NEW.assigned_role := 'admin'; NEW.route := 'manager';
                END IF;
                RETURN NEW;
              END; $$ LANGUAGE plpgsql;
              CREATE TRIGGER test_escalate_on_claim BEFORE UPDATE ON approvals
                FOR EACH ROW EXECUTE FUNCTION test_escalate_on_claim();");
        try
        {
            await body();
        }
        finally
        {
            await conn.ExecuteAsync("DROP TRIGGER IF EXISTS test_escalate_on_claim ON approvals");
        }
    }

    [Theory]
    [InlineData("approve")]
    [InlineData("reject")]
    [InlineData("request_info")]
    public async Task ASupportReviewer_CannotFinishAToolDecisionEscalatedAfterTheirCheck(string decision)
    {
        var (_, approval) = await SeedToolApprovalAsync("cancel_order", new { order_id = _orderId.ToString() });
        await using (var conn = await _pool.OpenAsync())
        {
            await conn.ExecuteAsync("UPDATE approvals SET assigned_role = 'support', route = 'support' WHERE id = @id", new { id = approval.Id });
        }
        using var support = SupportClient();

        HttpResponseMessage? response = null;
        await WithEscalationOnClaimAsync(async () =>
            response = await DecideAsync(support, approval.Id, new { decision, note = "because" }));

        response!.StatusCode.Should().Be(HttpStatusCode.Forbidden);
        var after = (await _repo.GetAsync(approval.Id))!;
        after.AssignedRole.Should().Be("admin", "the escalation did land between the read and the claim");
        after.Status.Should().Be(S.PendingReview);
        after.ClaimedBy.Should().BeNull("refused, and the claim is handed back");
        await using var check = await _pool.OpenAsync();
        (await check.ExecuteScalarAsync<string>("SELECT status FROM orders WHERE id = @id", new { id = _orderId })).Should().Be("placed");
    }

    [Fact]
    public async Task AManager_StillDecidesAfterTheSameEscalation()
    {
        var (_, approval) = await SeedToolApprovalAsync("cancel_order", new { order_id = _orderId.ToString() });
        await using (var conn = await _pool.OpenAsync())
        {
            await conn.ExecuteAsync("UPDATE approvals SET assigned_role = 'support', route = 'support' WHERE id = @id", new { id = approval.Id });
        }
        using var admin = AdminClient();

        HttpResponseMessage? response = null;
        await WithEscalationOnClaimAsync(async () => response = await DecideAsync(admin, approval.Id, new { decision = "approve" }));

        response!.EnsureSuccessStatusCode();
        (await StatusAsync(approval.Id)).Should().Be(S.Executed);
    }

    [Fact]
    public async Task ARetryAfterALostClaim_RecordsTheAlreadyExecutedAction_InsteadOfAnEndless409()
    {
        var (requestId, approval) = await SeedToolApprovalAsync("initiate_return", new { order_id = _orderId.ToString(), reason = "x" });
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync("UPDATE orders SET status = 'delivered' WHERE id = @id", new { id = _orderId });
        // The legacy record is closed (executed) in one statement; right after it, the claim is taken over: the recording then loses it.
        await conn.ExecuteAsync(
            @"CREATE OR REPLACE FUNCTION test_steal_claim() RETURNS trigger AS $$
              BEGIN
                IF NEW.status = 'executed' AND NEW.approval_id IS NOT NULL THEN
                  UPDATE approvals SET claimed_by = 'thief' WHERE id = NEW.approval_id;
                END IF;
                RETURN NEW;
              END; $$ LANGUAGE plpgsql;
              CREATE TRIGGER test_steal_claim AFTER UPDATE ON tool_approval_requests
                FOR EACH ROW EXECUTE FUNCTION test_steal_claim();");
        using var admin = AdminClient();
        HttpResponseMessage first;
        try
        {
            first = await DecideAsync(admin, approval.Id, new { decision = "approve" });
        }
        finally
        {
            await conn.ExecuteAsync("DROP TRIGGER IF EXISTS test_steal_claim ON tool_approval_requests");
        }
        first.StatusCode.Should().Be(HttpStatusCode.Conflict);
        (await StatusAsync(approval.Id)).Should().Be(S.PendingReview, "the first attempt could not record");
        (await conn.ExecuteScalarAsync<string>("SELECT status FROM tool_approval_requests WHERE id = @requestId", new { requestId }))
            .Should().Be("executed", "but the action ran");
        await conn.ExecuteAsync("UPDATE approvals SET claimed_by = NULL, claimed_at = NULL WHERE id = @id", new { id = approval.Id });

        var retry = await DecideAsync(admin, approval.Id, new { decision = "approve" });

        retry.EnsureSuccessStatusCode();
        (await StatusAsync(approval.Id)).Should().Be(S.Executed);
        (await conn.ExecuteScalarAsync<long>("SELECT count(*) FROM returns WHERE order_id = @id", new { id = _orderId }))
            .Should().Be(1, "the retry replays the recorded run; it never acts twice");
    }

    [Fact]
    public async Task AClaimLostWhileRecordingAWorkflowDecision_Is409NotA500()
    {
        var (_, _, approval) = await SeedWorkflowApprovalAsync();
        var mode = new StubMode
        {
            OnResume = async () =>
            {
                await using var conn = await _pool.OpenAsync();
                await conn.ExecuteAsync("UPDATE approvals SET claimed_by = 'thief' WHERE id = @id", new { id = approval.Id });
            },
        };
        using var support = SupportClient(mode);

        var response = await DecideAsync(support, approval.Id, new { decision = "approve" });

        response.StatusCode.Should().Be(HttpStatusCode.Conflict);
        (await JsonOf(response)).GetProperty("detail").GetString().Should().Be("This approval was taken over by another reviewer");
    }

    [Fact]
    public async Task ALegacyRequestWithPascalCaseNameAndCamelCaseKeys_IsMappedAndExecuted()
    {
        var (_, approval) = await SeedToolApprovalAsync("CancelOrder", new { orderId = _orderId.ToString(), Reason = "x" });
        using var admin = AdminClient();

        var response = await DecideAsync(admin, approval.Id, new { decision = "approve" });

        response.EnsureSuccessStatusCode();
        (await JsonOf(response)).GetProperty("result").GetProperty("success").GetBoolean().Should().BeTrue();
        (await StatusAsync(approval.Id)).Should().Be(S.Executed);
        await using var conn = await _pool.OpenAsync();
        (await conn.ExecuteScalarAsync<string>("SELECT status FROM orders WHERE id = @id", new { id = _orderId })).Should().Be("cancelled");
    }

    [Fact]
    public async Task ALegacyRequestWhoseKeysCollide_IsRefusedUpFront_FlaggedOnce_AndStaysDecidable()
    {
        var other = Guid.NewGuid().ToString();
        var (_, approval) = await SeedToolApprovalAsync("CancelOrder", new Dictionary<string, string>
        {
            ["orderId"] = _orderId.ToString(),
            ["order_id"] = other,
        });
        using var admin = AdminClient();

        var first = await DecideAsync(admin, approval.Id, new { decision = "approve" });
        var second = await DecideAsync(admin, approval.Id, new { decision = "approve" });

        first.StatusCode.Should().Be(HttpStatusCode.UnprocessableEntity);
        second.StatusCode.Should().Be(HttpStatusCode.UnprocessableEntity);
        (await JsonOf(first)).GetProperty("detail").GetString().Should().Contain("cannot be mapped unambiguously");
        var after = (await _repo.GetAsync(approval.Id))!;
        after.Status.Should().Be(S.PendingReview, "undecided, not half executed");
        after.ClaimedBy.Should().BeNull();
        (await _repo.ListEventsAsync(approval.Id)).Count(e => e.Action == "legacy_input_refused").Should().Be(1, "flagged once");
        await using var conn = await _pool.OpenAsync();
        (await conn.ExecuteScalarAsync<string>("SELECT status FROM orders WHERE id = @id", new { id = _orderId })).Should().Be("placed");

        // It can still be rejected, which is the way out.
        (await DecideAsync(admin, approval.Id, new { decision = "reject", note = "resubmit" })).EnsureSuccessStatusCode();
        (await StatusAsync(approval.Id)).Should().Be(S.Rejected);
    }

    // ───────────────────────────── a stub for the resume itself ─────────────────────────────

    private sealed class StubMode : IOrchestrationMode, IResumableMode
    {
        private int _calls;
        public int Calls => Volatile.Read(ref _calls);
        public TimeSpan? Delay { get; init; }
        public bool? Completed { get; init; }
        public Exception? FailWith { get; init; }
        public Func<Task>? OnResume { get; init; }
        public string? SeenEmail { get; private set; }
        public string? SeenRole { get; private set; }
        public ReturnDecision? LastDecision { get; private set; }

        public string Name => "workflow:return-replace";
        public string Label => "Return & Replace";
        public string Description => "test";
        public ModeCapabilities Capabilities => new(SupportsHitl: true);
        public string? GraphMermaid() => null;

        public Task<ModeRunResult> RunAsync(string message, RunContext ctx, CancellationToken ct = default) =>
            Task.FromResult(new ModeRunResult("ran", ["orchestrator"], 1));

        public Task<ModeRunResult> ResumeAsync(string sessionId, string checkpointId, string requestId, bool approved, CancellationToken ct = default) =>
            ResumeAsync(sessionId, checkpointId, requestId, ReturnDecision.FromApproved(approved), ct);

        public async Task<ModeRunResult> ResumeAsync(
            string sessionId, string checkpointId, string requestId, ReturnDecision decision, CancellationToken ct = default)
        {
            Interlocked.Increment(ref _calls);
            if (Delay is { } d) await Task.Delay(d, ct);
            if (FailWith is not null) throw FailWith;
            if (OnResume is { } hook) await hook();
            SeenEmail = RequestContext.CurrentUserEmail;
            SeenRole = RequestContext.CurrentUserRole;
            LastDecision = decision;
            return new ModeRunResult(
                decision.IsApproval ? "Return approved." : "Return rejected.", ["order-management"], 3, Completed: Completed);
        }
    }
}
