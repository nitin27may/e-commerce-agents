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
/// <c>/api/admin/hitl/*</c> (<see cref="HitlRoutes"/>) — the admin surface
/// for the tool-approval queue, plus <see cref="HitlActionExecutor"/>'s
/// direct-DB-mutation dispatch on approve. Mirrors Python's
/// <c>list_hitl_requests</c>/<c>approve_hitl_request</c>/
/// <c>deny_hitl_request</c> (<c>routes.py:1199-1296</c>, <c>shared/hitl.py</c>).
/// Seeds <c>tool_approval_requests</c> rows directly (bypassing the tool
/// call itself — that gate is covered by OrderManagement/InventoryFulfillment's
/// own HITL gate tests) so this file is purely about the admin queue and the
/// approved-action dispatch table.
/// </summary>
[Collection(nameof(LocalPostgresCollection))]
public sealed class HitlRoutesTests : IAsyncLifetime
{
    private readonly PostgresFixture _pg;
    private DatabasePool _pool = null!;
    private const string CustomerEmail = "hitl-customer@example.com";
    private const string AdminEmail = "hitl-admin@example.com";
    private Guid _customerId;
    private Guid _placedOrderId;

    public HitlRoutesTests(PostgresFixture pg) => _pg = pg;

    public async Task InitializeAsync()
    {
        var settings = new AgentSettings { DatabaseUrl = _pg.ConnectionString };
        _pool = new DatabasePool(settings);
        await SeedAsync();
    }

    public async Task DisposeAsync() => await _pool.DisposeAsync();

    private async Task SeedAsync()
    {
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            @"TRUNCATE order_status_history, order_items, returns, orders,
                       tool_approval_requests, products, users
              RESTART IDENTITY CASCADE"
        );
        _customerId = await conn.ExecuteScalarAsync<Guid>(
            "INSERT INTO users (email, password_hash, name, role) VALUES (@e, 'x', 'Customer', 'customer') RETURNING id",
            new { e = CustomerEmail }
        );
        const string addr = "{\"street\":\"1 Test\",\"city\":\"SF\",\"state\":\"CA\",\"zip\":\"94105\",\"country\":\"US\"}";
        _placedOrderId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO orders (user_id, status, total, shipping_address)
              VALUES (@uid, 'placed', 199.99, @addr::jsonb) RETURNING id",
            new { uid = _customerId, addr }
        );
    }

    private async Task<Guid> SeedPendingRequestAsync(string toolName, object toolInput)
    {
        await using var conn = await _pool.OpenAsync();
        return await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO tool_approval_requests (user_email, agent_name, tool_name, tool_input)
              VALUES (@email, 'order-management', @tool, @input::jsonb)
              RETURNING id",
            new { email = CustomerEmail, tool = toolName, input = JsonSerializer.Serialize(toolInput) }
        );
    }

    private HttpClient AdminClient()
    {
        var server = OrchestratorTestHost.Create(_pool, r => r.MapHitlRoutes());
        var client = server.CreateClient();
        client.DefaultRequestHeaders.Add("X-Test-Email", AdminEmail);
        client.DefaultRequestHeaders.Add("X-Test-Role", "admin");
        return client;
    }

    private HttpClient CustomerClient()
    {
        var server = OrchestratorTestHost.Create(_pool, r => r.MapHitlRoutes());
        var client = server.CreateClient();
        client.DefaultRequestHeaders.Add("X-Test-Email", CustomerEmail);
        client.DefaultRequestHeaders.Add("X-Test-Role", "customer");
        return client;
    }

    // ─────────────────────── list ────────────────────────────

    [Fact]
    public async Task ListRequests_RejectsNonAdmin()
    {
        await SeedPendingRequestAsync("cancel_order", new { order_id = _placedOrderId.ToString(), reason = "x" });
        using var client = CustomerClient();
        var response = await client.GetAsync("/api/admin/hitl/requests");
        response.StatusCode.Should().Be(HttpStatusCode.Forbidden);
    }

    [Fact]
    public async Task ListRequests_FiltersByStatus()
    {
        await SeedPendingRequestAsync("cancel_order", new { order_id = _placedOrderId.ToString(), reason = "x" });
        using var admin = AdminClient();

        var pending = await admin.GetFromJsonAsync<JsonElement>("/api/admin/hitl/requests?status=pending");
        pending.GetProperty("total").GetInt32().Should().Be(1);

        var denied = await admin.GetFromJsonAsync<JsonElement>("/api/admin/hitl/requests?status=denied");
        denied.GetProperty("total").GetInt32().Should().Be(0);
    }

    // ─────────────────────── approve: cancel_order ───────────

    [Fact]
    public async Task Approve_CancelOrder_ActuallyCancelsOrderAndMarksExecuted()
    {
        var requestId = await SeedPendingRequestAsync(
            "cancel_order",
            new { order_id = _placedOrderId.ToString(), reason = "changed my mind" }
        );
        using var admin = AdminClient();

        var response = await admin.PostAsJsonAsync($"/api/admin/hitl/requests/{requestId}/approve", new { note = "ok" });
        response.EnsureSuccessStatusCode();
        var payload = await response.Content.ReadFromJsonAsync<JsonElement>();
        payload.GetProperty("status").GetString().Should().Be("approved");
        payload.GetProperty("execution_result").GetProperty("success").GetBoolean().Should().BeTrue();

        await using var conn = await _pool.OpenAsync();
        var orderStatus = await conn.ExecuteScalarAsync<string>(
            "SELECT status FROM orders WHERE id = @id", new { id = _placedOrderId }
        );
        orderStatus.Should().Be("cancelled");

        var (reqStatus, approvedBy) = await conn.QueryFirstAsync<(string status, string approved_by)>(
            "SELECT status, approved_by FROM tool_approval_requests WHERE id = @id", new { id = requestId }
        );
        reqStatus.Should().Be("executed"); // matches Python: always "executed" on approve, regardless of success
        approvedBy.Should().Be(AdminEmail);
    }

    /// <summary>
    /// Regression for the duplicate-refund window (#28).
    ///
    /// Approve used to read the status, check it was pending, **execute the
    /// destructive action**, and only then UPDATE ... WHERE status = 'pending'.
    /// Two concurrent approvals both passed the pre-check and both executed;
    /// only the loser's UPDATE failed, by which point the action had already
    /// happened twice. Python fixed this by claiming the row atomically first
    /// (shared/hitl.py::claim_hitl_request); this asserts .NET does too.
    ///
    /// The assertion is on the *side effect*, not the row status — a status
    /// check alone passed even with the bug present.
    /// </summary>
    [Fact]
    public async Task Approve_ConcurrentApprovals_ExecuteTheActionExactlyOnce()
    {
        // initiate_return, not cancel_order: cancel_order's UPDATE is
        // self-guarding (WHERE status IN ('placed','confirmed')), so a second
        // execution is a no-op and the bug leaves no trace. initiate_return is
        // a bare INSERT INTO returns with no duplicate guard, so a second
        // execution leaves a second row — which is exactly the shape of the
        // duplicate refund Python's fix was written to prevent.
        var requestId = await SeedPendingRequestAsync(
            "initiate_return",
            new { order_id = _placedOrderId.ToString(), reason = "double-click" }
        );

        using var adminA = AdminClient();
        using var adminB = AdminClient();

        // Fire both approvals at the same instant against the same row.
        var responses = await Task.WhenAll(
            adminA.PostAsJsonAsync($"/api/admin/hitl/requests/{requestId}/approve", new { note = "A" }),
            adminB.PostAsJsonAsync($"/api/admin/hitl/requests/{requestId}/approve", new { note = "B" })
        );

        // Exactly one wins; the other is refused rather than silently executing.
        responses.Count(r => r.IsSuccessStatusCode).Should().Be(1,
            "only one approval may reach the executor");

        await using var conn = await _pool.OpenAsync();

        // The real proof, and the assertion that fails against the old
        // check-then-execute-then-claim ordering.
        var returns = await conn.ExecuteScalarAsync<long>(
            "SELECT COUNT(*) FROM returns WHERE order_id = @id", new { id = _placedOrderId }
        );
        returns.Should().Be(1, "the destructive action must run exactly once");

        var reqStatus = await conn.ExecuteScalarAsync<string>(
            "SELECT status FROM tool_approval_requests WHERE id = @id", new { id = requestId }
        );
        reqStatus.Should().Be("executed");
    }

    [Fact]
    public async Task Approve_UnknownOrder_StillMarksExecutedWithFailureInResult()
    {
        // Matches Python precisely: execute_approved_action's failure dict is
        // still non-empty/truthy, so resolve_hitl_request's final_status is
        // "executed" even though execution_result.success is false.
        var requestId = await SeedPendingRequestAsync(
            "cancel_order",
            new { order_id = Guid.NewGuid().ToString(), reason = "x" }
        );
        using var admin = AdminClient();

        var response = await admin.PostAsJsonAsync($"/api/admin/hitl/requests/{requestId}/approve", new { });
        response.EnsureSuccessStatusCode();
        var payload = await response.Content.ReadFromJsonAsync<JsonElement>();
        payload.GetProperty("execution_result").GetProperty("success").GetBoolean().Should().BeFalse();

        await using var conn = await _pool.OpenAsync();
        var reqStatus = await conn.ExecuteScalarAsync<string>(
            "SELECT status FROM tool_approval_requests WHERE id = @id", new { id = requestId }
        );
        reqStatus.Should().Be("executed");
    }

    [Fact]
    public async Task Approve_AlreadyResolved_Returns400()
    {
        var requestId = await SeedPendingRequestAsync(
            "cancel_order",
            new { order_id = _placedOrderId.ToString(), reason = "x" }
        );
        using var admin = AdminClient();

        var first = await admin.PostAsJsonAsync($"/api/admin/hitl/requests/{requestId}/approve", new { });
        first.EnsureSuccessStatusCode();

        var second = await admin.PostAsJsonAsync($"/api/admin/hitl/requests/{requestId}/approve", new { });
        second.StatusCode.Should().Be(HttpStatusCode.BadRequest);
    }

    [Fact]
    public async Task Approve_UnknownRequestId_Returns404()
    {
        using var admin = AdminClient();
        var response = await admin.PostAsJsonAsync($"/api/admin/hitl/requests/{Guid.NewGuid()}/approve", new { });
        response.StatusCode.Should().Be(HttpStatusCode.NotFound);
    }

    // ─────────────────────── deny ─────────────────────────────

    [Fact]
    public async Task Deny_LeavesOrderUntouchedAndMarksDenied()
    {
        var requestId = await SeedPendingRequestAsync(
            "cancel_order",
            new { order_id = _placedOrderId.ToString(), reason = "x" }
        );
        using var admin = AdminClient();

        var response = await admin.PostAsJsonAsync($"/api/admin/hitl/requests/{requestId}/deny", new { note = "no" });
        response.EnsureSuccessStatusCode();
        var payload = await response.Content.ReadFromJsonAsync<JsonElement>();
        payload.GetProperty("status").GetString().Should().Be("denied");

        await using var conn = await _pool.OpenAsync();
        var orderStatus = await conn.ExecuteScalarAsync<string>(
            "SELECT status FROM orders WHERE id = @id", new { id = _placedOrderId }
        );
        orderStatus.Should().Be("placed"); // untouched — deny never executes the action

        var reqStatus = await conn.ExecuteScalarAsync<string>(
            "SELECT status FROM tool_approval_requests WHERE id = @id", new { id = requestId }
        );
        reqStatus.Should().Be("denied");
    }

    // ─────────────────────── other gated tools ────────────────

    [Fact]
    public async Task Approve_InitiateReturn_CreatesReturnRow()
    {
        await using (var conn = await _pool.OpenAsync())
        {
            await conn.ExecuteAsync("UPDATE orders SET status = 'delivered' WHERE id = @id", new { id = _placedOrderId });
        }
        var requestId = await SeedPendingRequestAsync(
            "initiate_return",
            new { order_id = _placedOrderId.ToString(), reason = "wrong size" }
        );
        using var admin = AdminClient();

        var response = await admin.PostAsJsonAsync($"/api/admin/hitl/requests/{requestId}/approve", new { });
        response.EnsureSuccessStatusCode();
        var payload = await response.Content.ReadFromJsonAsync<JsonElement>();
        payload.GetProperty("execution_result").GetProperty("success").GetBoolean().Should().BeTrue();

        await using var conn2 = await _pool.OpenAsync();
        var returnCount = await conn2.ExecuteScalarAsync<long>(
            "SELECT COUNT(*) FROM returns WHERE order_id = @id AND status = 'approved'",
            new { id = _placedOrderId }
        );
        returnCount.Should().Be(1);
    }

    [Fact]
    public async Task Approve_UnconfiguredTool_ReturnsFailureMessage()
    {
        var requestId = await SeedPendingRequestAsync("some_future_tool", new { });
        using var admin = AdminClient();

        var response = await admin.PostAsJsonAsync($"/api/admin/hitl/requests/{requestId}/approve", new { });
        response.EnsureSuccessStatusCode();
        var payload = await response.Content.ReadFromJsonAsync<JsonElement>();
        payload.GetProperty("execution_result").GetProperty("success").GetBoolean().Should().BeFalse();
        payload.GetProperty("execution_result").GetProperty("message").GetString()
            .Should().Contain("Auto-execution not configured");
    }
}
