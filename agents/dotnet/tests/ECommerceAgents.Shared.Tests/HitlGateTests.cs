using Dapper;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Middleware;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// <see cref="HitlGate"/> — the interception layer human-in-the-loop
/// approval moved into (issue #17, piece 2 of 3), replacing
/// <c>HitlApprovalMiddleware.GuardAsync</c>'s call-site-wrapper design.
/// Exercises <see cref="HitlGate.TryGateAsync"/> directly against a real
/// Postgres testcontainer, the same "never mock the DB" convention
/// <c>OrderToolsTests</c>/<c>InventoryToolsTests</c> already follow —
/// gating no longer happens inside those tools' own method bodies, so
/// this is where the gate itself gets covered now.
/// </summary>
[Collection(nameof(LocalPostgresCollection))]
public sealed class HitlGateTests : IAsyncLifetime
{
    private readonly PostgresFixture _pg;
    private DatabasePool _pool = null!;
    private const string Email = "gatekeeper@example.com";

    public HitlGateTests(PostgresFixture pg) => _pg = pg;

    public async Task InitializeAsync()
    {
        var settings = new AgentSettings { DatabaseUrl = _pg.ConnectionString };
        _pool = new DatabasePool(settings);
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            "INSERT INTO users (email, password_hash, name, role) VALUES (@email, 'x', 'Gatekeeper', 'customer') ON CONFLICT DO NOTHING",
            new { email = Email }
        );
    }

    public async Task DisposeAsync()
    {
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync("TRUNCATE tool_approval_requests, users RESTART IDENTITY CASCADE");
        await _pool.DisposeAsync();
    }

    /// <summary>A placed order owned by <see cref="Email"/>: the gate's pre-park check refuses a cancel of anything else.</summary>
    private async Task<string> PlacedOrderAsync()
    {
        await using var conn = await _pool.OpenAsync();
        var id = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO orders (user_id, status, total, shipping_address)
              SELECT id, 'placed', 500, '{}'::jsonb FROM users WHERE email = @email RETURNING id",
            new { email = Email });
        return id.ToString();
    }

    private HitlGate BuildGate(bool hitlEnabled = true) =>
        new(_pool, new AgentSettings { DatabaseUrl = _pg.ConnectionString, HitlEnabled = hitlEnabled });

    [Fact]
    public async Task TryGateAsync_GatedTool_CreatesPendingRequestAndReturnsPendingApproval()
    {
        RequestContext.CurrentUserEmail = Email;
        var gate = BuildGate();

        var result = await gate.TryGateAsync("CancelOrder", "order-management", new { order_id = await PlacedOrderAsync(), reason = "wrong size" });

        result.Should().NotBeNull();

        await using var conn = await _pool.OpenAsync();
        var row = (await conn.QueryFirstOrDefaultAsync(
            "SELECT tool_name, agent_name, user_email, status FROM tool_approval_requests WHERE tool_name = 'cancel_order'"
        ))!;
        // Stored snake_case, as Python stores it: the policy kinds, the executor and the other stack's approve path all key on it.
        // (It used to be the pipeline's PascalCase name, which the executor then could not dispatch.)
        ((string)row.tool_name).Should().Be("cancel_order");
        ((string)row.agent_name).Should().Be("order-management");
        ((string)row.user_email).Should().Be(Email);
        ((string)row.status).Should().Be("pending");
    }

    /// <summary>
    /// D-5n: a gated call opens the inbox row beside the legacy request — kind and input in the shape the policy and Python read, routed
    /// by the policy, linked back through <c>approval_id</c>, with the SLA timers written in the same transaction.
    /// </summary>
    [Fact]
    public async Task TryGateAsync_OpensAPolicyRoutedApproval_LinkedToTheRequest_WithItsTimers()
    {
        RequestContext.CurrentUserEmail = Email;
        var gate = BuildGate();

        await gate.TryGateAsync("CancelOrder", "order-management", new { orderId = await PlacedOrderAsync(), reason = "x" });

        await using var conn = await _pool.OpenAsync();
        var request = await conn.QueryFirstAsync(
            "SELECT id, tool_input::text AS input, approval_id FROM tool_approval_requests WHERE tool_name = 'cancel_order'");
        ((string)request.input).Should().Contain("\"order_id\"", "top-level input keys are snake_case, as the executor and Python read them");
        ((Guid?)request.approval_id).Should().NotBeNull();

        var approval = await conn.QueryFirstAsync(
            "SELECT source, source_ref, kind, requester_email, status, route, assigned_role, due_at, policy_version FROM approvals WHERE id = @id",
            new { id = (Guid)request.approval_id });
        ((string)approval.source).Should().Be("tool");
        ((Guid)approval.source_ref).Should().Be((Guid)request.id);
        ((string)approval.kind).Should().Be("cancel_order");
        ((string)approval.requester_email).Should().Be(Email);
        ((string)approval.status).Should().Be("pending_review");
        ((string)approval.route).Should().BeOneOf("support", "manager");
        ((string)approval.assigned_role).Should().BeOneOf("support", "admin");
        ((DateTime?)approval.due_at).Should().NotBeNull();
        ((string?)approval.policy_version).Should().NotBeNullOrEmpty();

        var timers = await conn.QueryAsync<string>(
            "SELECT topic FROM outbox WHERE aggregate_id = @id ORDER BY topic", new { id = (Guid)request.approval_id });
        timers.Should().Equal("approval.reminder_due", "approval.sla_breached");
    }

    [Theory]
    [InlineData("CancelOrder")]
    [InlineData("ModifyOrder")]
    [InlineData("PlaceBackorder")]
    [InlineData("InitiateReturn")]
    [InlineData("ProcessRefund")]
    [InlineData("cancel_order")]
    public async Task TryGateAsync_RecognizesEveryGatedTool_ByEitherName(string toolName)
    {
        RequestContext.CurrentUserEmail = Email;
        var gate = BuildGate();

        // An empty input is refused by the pre-park check (nothing to act on), which still shows the tool was recognized as
        // gated: a non-gated tool returns null.
        var result = await gate.TryGateAsync(toolName, "some-agent", new { });

        result.Should().NotBeNull();
    }

    [Fact]
    public async Task TryGateAsync_NonGatedTool_ReturnsNullAndCreatesNoRequest()
    {
        RequestContext.CurrentUserEmail = Email;
        var gate = BuildGate();

        var result = await gate.TryGateAsync("GetOrderDetails", "order-management", new { order_id = "abc" });

        result.Should().BeNull();

        await using var conn = await _pool.OpenAsync();
        var count = await conn.ExecuteScalarAsync<long>("SELECT COUNT(*) FROM tool_approval_requests");
        count.Should().Be(0);
    }

    [Fact]
    public async Task TryGateAsync_HitlDisabled_ReturnsNullEvenForAGatedTool()
    {
        RequestContext.CurrentUserEmail = Email;
        var gate = BuildGate(hitlEnabled: false);

        var result = await gate.TryGateAsync("CancelOrder", "order-management", new { order_id = "abc" });

        result.Should().BeNull();

        await using var conn = await _pool.OpenAsync();
        var count = await conn.ExecuteScalarAsync<long>("SELECT COUNT(*) FROM tool_approval_requests");
        count.Should().Be(0);
    }

    [Fact]
    public async Task TryGateAsync_DatabaseFailure_FailsClosed()
    {
        // Fail-closed regression (a real bug the call-site wrapper this
        // replaced had — it failed open on a DB error, contradicting
        // Python's own hitl.py). Point at an unreachable Postgres instance
        // so the INSERT genuinely throws, then assert the gate still
        // returns a non-null short-circuit result rather than null (which
        // would let the caller fall through and execute the gated tool
        // unapproved).
        RequestContext.CurrentUserEmail = Email;
        var brokenSettings = new AgentSettings { DatabaseUrl = "postgresql://nouser:nopass@localhost:1/nonexistent" };
        var brokenPool = new DatabasePool(brokenSettings);
        var gate = new HitlGate(brokenPool, new AgentSettings { HitlEnabled = true });

        var result = await gate.TryGateAsync("CancelOrder", "order-management", new { order_id = Guid.NewGuid().ToString() });

        result.Should().NotBeNull("a DB failure must fail closed, not silently let the tool execute");
        await brokenPool.DisposeAsync();
    }
}
