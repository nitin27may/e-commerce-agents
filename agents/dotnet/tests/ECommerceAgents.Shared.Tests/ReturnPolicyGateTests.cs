using Dapper;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Policy;
using ECommerceAgents.Shared.Workflows;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// D-8 — the return workflow's policy gate against the real engine and real Postgres (the .NET counterpart of
/// Python's <c>tests/test_return_policy_gate.py</c>): facts come from the requester's own rows, the shipped
/// <c>approvals.yaml</c> decides, and the amount adjuster the workflow applies after an edit is identity-scoped.
/// </summary>
[Collection(nameof(LocalPostgresCollection))]
public sealed class ReturnPolicyGateTests : IAsyncLifetime
{
    private const string Email = "gate.customer@example.com";

    private readonly PostgresFixture _pg;
    private DatabasePool _pool = null!;
    private AgentSettings _settings = null!;
    private Guid _userId;

    public ReturnPolicyGateTests(PostgresFixture pg) => _pg = pg;

    public async Task InitializeAsync()
    {
        _settings = new AgentSettings { DatabaseUrl = _pg.ConnectionString };
        _pool = new DatabasePool(_settings);
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync("TRUNCATE order_status_history, returns, order_items, orders, users RESTART IDENTITY CASCADE");
        _userId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO users (email, password_hash, name, role, total_spend, created_at)
              VALUES (@e, 'x', 'Gate', 'customer', 2000, NOW() - interval '200 days') RETURNING id",
            new { e = Email });
    }

    public async Task DisposeAsync() => await _pool.DisposeAsync();

    private static string PolicyPath()
    {
        var dir = new DirectoryInfo(Directory.GetCurrentDirectory());
        while (dir is not null && !File.Exists(Path.Combine(dir.FullName, "agents", "python", "config", "policies", "approvals.yaml")))
        {
            dir = dir.Parent;
        }
        return Path.Combine(dir!.FullName, "agents", "python", "config", "policies", "approvals.yaml");
    }

    private static ReturnPolicyGate Gate(DatabasePool pool) => new(ApprovalPolicy.LoadFile(PolicyPath()), pool);

    private async Task<string> DeliveredOrderAsync(decimal total, int deliveredDaysAgo = 5)
    {
        await using var conn = await _pool.OpenAsync();
        var id = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO orders (user_id, status, total, shipping_address)
              VALUES (@u, 'delivered', @t, '{}'::jsonb) RETURNING id",
            new { u = _userId, t = total });
        await conn.ExecuteAsync(
            @"INSERT INTO order_status_history (order_id, status, timestamp)
              VALUES (@id, 'delivered', NOW() - make_interval(days => @d))",
            new { id, d = deliveredDaysAgo });
        return id.ToString();
    }

    [Fact]
    public async Task ASmallInGoodStandingReturn_IsAutoApprovedByTheEngine()
    {
        RequestContext.CurrentUserEmail = Email; // AsyncLocal: set in the test body, not in InitializeAsync
        var order = await DeliveredOrderAsync(30m);

        var decision = await Gate(_pool).DecideAsync(new WorkflowState(Email, order) { OrderTotal = 30m });

        decision.Should().NotBeNull();
        decision!.Route.Should().Be("auto");
        decision.Evidence["rule"].Should().Be("auto-small-good-standing");
    }

    [Fact]
    public async Task ALargeReturn_IsRoutedToAManager_WithTheEnginesEvidence()
    {
        RequestContext.CurrentUserEmail = Email; // AsyncLocal: set in the test body, not in InitializeAsync
        var order = await DeliveredOrderAsync(649.99m);

        var decision = await Gate(_pool).DecideAsync(new WorkflowState(Email, order) { OrderTotal = 649.99m });

        decision!.Route.Should().Be("manager");
        decision.Evidence["rule"].Should().Be("high-value");
        var facts = (IReadOnlyDictionary<string, object?>)decision.Evidence["facts"]!;
        facts["days_since_delivery"].Should().Be(5d);
    }

    [Fact]
    public async Task WithoutAnIdentity_TheEngineCannotDecide_SoTheGateUsesTheThreshold()
    {
        RequestContext.CurrentUserEmail = "";
        var order = await DeliveredOrderAsync(30m);

        var decision = await Gate(_pool).DecideAsync(new WorkflowState(Email, order) { OrderTotal = 30m });

        decision.Should().BeNull();
    }

    [Fact]
    public async Task SomeoneElsesOrder_ContributesNoFacts_SoItCannotBeAutoApproved()
    {
        var order = await DeliveredOrderAsync(30m);
        RequestContext.CurrentUserEmail = "intruder@example.com";

        var decision = await Gate(_pool).DecideAsync(new WorkflowState(Email, order) { OrderTotal = 30m });

        // The requester does not exist, so every fact is unknown and an auto rule can never fire.
        decision.Should().NotBeNull();
        decision!.Route.Should().Be("support");
    }

    [Fact]
    public void TheDemoToursReturn_PausesForAManager_UnderTheDefaultPolicy()
    {
        // tour@example.com's delivered Dyson V15 ($649.99, delivered 5 days ago): over 500 -> a manager, so the tour
        // pauses for approval as it must, and the approval step has to be done as an admin.
        var policy = ApprovalPolicy.LoadFile(PolicyPath());

        var decision = policy.Evaluate(ReturnPolicyGate.Kind, new Dictionary<string, object?>
        {
            ["amount"] = 649.99,
            ["days_since_delivery"] = 5,
            ["loyalty_tier"] = "bronze",
            ["returns_90d"] = 0,
            ["refund_to_spend_ratio"] = null,
            ["account_age_days"] = 0,
        });

        decision.Route.Should().Be("manager");
    }

    // ── AdjustReturnAmountAsync: identity-scoped, never raises a refund ──────────────

    private async Task<string> ReturnAsync(decimal refund)
    {
        var order = await DeliveredOrderAsync(refund);
        await using var conn = await _pool.OpenAsync();
        var id = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO returns (order_id, user_id, reason, status, refund_method, refund_amount)
              VALUES (@o, @u, 'r', 'requested', 'store_credit', @a) RETURNING id",
            new { o = Guid.Parse(order), u = _userId, a = refund });
        return id.ToString();
    }

    private async Task<decimal> RefundAsync(string returnId)
    {
        await using var conn = await _pool.OpenAsync();
        return await conn.ExecuteScalarAsync<decimal>(
            "SELECT refund_amount FROM returns WHERE id = @id", new { id = Guid.Parse(returnId) });
    }

    [Fact]
    public async Task AdjustReturnAmount_LowersTheRequestersOwnRefund()
    {
        RequestContext.CurrentUserEmail = Email; // AsyncLocal: set in the test body, not in InitializeAsync
        var returnId = await ReturnAsync(100m);

        var outcome = await new ReturnReplaceTools(_pool, _settings).AdjustReturnAmountAsync(returnId, 40m);

        outcome.Success.Should().BeTrue();
        (await RefundAsync(returnId)).Should().Be(40m);
    }

    [Theory]
    [InlineData(100.01)]
    [InlineData(-1)]
    public async Task AdjustReturnAmount_RefusesARaiseOrANegative(double amount)
    {
        RequestContext.CurrentUserEmail = Email; // AsyncLocal: set in the test body, not in InitializeAsync
        var returnId = await ReturnAsync(100m);

        var outcome = await new ReturnReplaceTools(_pool, _settings).AdjustReturnAmountAsync(returnId, (decimal)amount);

        outcome.Success.Should().BeFalse();
        (await RefundAsync(returnId)).Should().Be(100m);
    }

    [Fact]
    public async Task AdjustReturnAmount_CannotTouchSomeoneElsesReturn()
    {
        var returnId = await ReturnAsync(100m);
        RequestContext.CurrentUserEmail = "intruder@example.com";

        var outcome = await new ReturnReplaceTools(_pool, _settings).AdjustReturnAmountAsync(returnId, 10m);

        outcome.Success.Should().BeFalse();
        (await RefundAsync(returnId)).Should().Be(100m);
    }
}
