using Dapper;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Middleware;
using ECommerceAgents.Shared.Tools;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using System.Text.Json;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// Returns and loyalty (#18, #33 PR 5b) — the last of the shared tool library,
/// and the two most destructive tools in it.
/// </summary>
[Collection(nameof(LocalPostgresCollection))]
public sealed class ReturnAndLoyaltyToolsTests : IAsyncLifetime
{
    private const string CustomerEmail = "returns.tools@example.com";

    private readonly PostgresFixture _pg;
    private DatabasePool _pool = null!;
    private AgentSettings _settings = null!;
    private Guid _userId;
    private Guid _deliveredOrderId;
    private Guid _placedOrderId;

    public ReturnAndLoyaltyToolsTests(PostgresFixture pg) => _pg = pg;

    public async Task InitializeAsync()
    {
        _settings = new AgentSettings { DatabaseUrl = _pg.ConnectionString };
        _pool = new DatabasePool(_settings);
        await SeedAsync();
    }

    public async Task DisposeAsync() => await _pool.DisposeAsync();

    private async Task SeedAsync()
    {
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            @"TRUNCATE order_status_history, returns, order_items, orders,
                       products, loyalty_tiers, users RESTART IDENTITY CASCADE"
        );
        await conn.ExecuteAsync(
            @"INSERT INTO loyalty_tiers (name, min_spend, discount_pct, free_shipping_threshold, priority_support)
              VALUES ('bronze', 0, 0, NULL, FALSE), ('gold', 3000, 10, 0, TRUE)"
        );
        _userId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO users (email, password_hash, name, role, loyalty_tier, total_spend)
              VALUES (@e, 'x', 'Returns User', 'customer', 'gold', 4200) RETURNING id",
            new { e = CustomerEmail }
        );

        const string addr = "{\"street\":\"1 Test\",\"city\":\"SF\",\"state\":\"CA\",\"zip\":\"94105\",\"country\":\"US\"}";
        _deliveredOrderId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO orders (user_id, status, total, shipping_address)
              VALUES (@u, 'delivered', 250.00, @a::jsonb) RETURNING id",
            new { u = _userId, a = addr }
        );
        _placedOrderId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO orders (user_id, status, total, shipping_address)
              VALUES (@u, 'placed', 99.00, @a::jsonb) RETURNING id",
            new { u = _userId, a = addr }
        );
        await conn.ExecuteAsync(
            @"INSERT INTO order_status_history (order_id, status, timestamp)
              VALUES (@o, 'delivered', NOW() - INTERVAL '5 days')",
            new { o = _deliveredOrderId }
        );
    }

    /// <summary>
    /// Identity is set here rather than in InitializeAsync: RequestContext is
    /// AsyncLocal, and a value set inside an async method does not flow back
    /// out to its caller — so setting it during setup left each test running
    /// on whatever identity the previous test class happened to leave behind,
    /// which passed or failed depending on execution order.
    /// </summary>
    private void SignIn()
    {
        RequestContext.CurrentUserEmail = CustomerEmail;
        RequestContext.CurrentUserRole = "customer";
    }

    private ReturnTools Returns()
    {
        SignIn();
        return new ReturnTools(_pool, _settings);
    }

    private LoyaltyTools Loyalty()
    {
        SignIn();
        return new LoyaltyTools(_pool);
    }
    private static JsonElement Json(object v) => JsonSerializer.SerializeToElement(v);

    // ─────────────────────── eligibility ───────────────────────

    [Fact]
    public async Task CheckReturnEligibility_DeliveredWithinWindow_IsEligible()
    {
        var result = Json(await Returns().CheckReturnEligibility(_deliveredOrderId.ToString()));

        result.GetProperty("eligible").GetBoolean().Should().BeTrue();
        result.GetProperty("days_remaining").GetInt32().Should().Be(25, "delivered 5 days into a 30-day window");
    }

    [Fact]
    public async Task CheckReturnEligibility_NotDelivered_IsRefusedWithTheReason()
    {
        var result = Json(await Returns().CheckReturnEligibility(_placedOrderId.ToString()));

        result.GetProperty("eligible").GetBoolean().Should().BeFalse();
        result.GetProperty("reason").GetString().Should().Contain("delivered");
    }

    [Fact]
    public async Task CheckReturnEligibility_ExpiredWindow_IsRefused()
    {
        await using (var conn = await _pool.OpenAsync())
        {
            await conn.ExecuteAsync(
                "UPDATE order_status_history SET timestamp = NOW() - INTERVAL '45 days' WHERE order_id = @o",
                new { o = _deliveredOrderId }
            );
        }

        var result = Json(await Returns().CheckReturnEligibility(_deliveredOrderId.ToString()));

        result.GetProperty("eligible").GetBoolean().Should().BeFalse();
        result.GetProperty("reason").GetString().Should().Contain("window expired");
    }

    /// <summary>
    /// Every query is scoped by the caller's own email, so passing someone
    /// else's order id must read as "not found" rather than leaking that it
    /// exists.
    /// </summary>
    [Fact]
    public async Task CheckReturnEligibility_AnotherUsersOrder_IsNotFound()
    {
        Guid otherOrder;
        await using (var conn = await _pool.OpenAsync())
        {
            var otherUser = await conn.ExecuteScalarAsync<Guid>(
                "INSERT INTO users (email, password_hash, name, role) VALUES ('someone.else@example.com','x','Other','customer') RETURNING id"
            );
            otherOrder = await conn.ExecuteScalarAsync<Guid>(
                @"INSERT INTO orders (user_id, status, total, shipping_address)
                  VALUES (@u, 'delivered', 10, '{}'::jsonb) RETURNING id",
                new { u = otherUser }
            );
        }

        var result = Json(await Returns().CheckReturnEligibility(otherOrder.ToString()));

        result.GetProperty("error").GetString().Should().Contain("not found or access denied");
    }

    // ─────────────────────── initiate / refund ───────────────────────

    [Fact]
    public async Task InitiateReturn_CreatesTheReturnWithTheOrderTotal()
    {
        var result = Json(await Returns().InitiateReturn(_deliveredOrderId.ToString(), "Faulty"));

        result.GetProperty("status").GetString().Should().Be("requested");
        result.GetProperty("refund_amount").GetDecimal().Should().Be(250.00m);
    }

    /// <summary>
    /// .NET has no idempotency store yet (#30's backstop), so the one-return-
    /// per-order guard in the INSERT is the only thing between a double-click
    /// and two refunds.
    /// </summary>
    [Fact]
    public async Task InitiateReturn_Twice_CreatesOnlyOneReturn()
    {
        await Returns().InitiateReturn(_deliveredOrderId.ToString(), "Faulty");
        var second = Json(await Returns().InitiateReturn(_deliveredOrderId.ToString(), "Faulty again"));

        // The refusal comes back as the eligibility payload ("a return already
        // exists"), not an error string — InitiateReturn re-checks eligibility
        // rather than trusting the model to have done it.
        second.TryGetProperty("status", out _).Should().BeFalse("a second return must not succeed");
        second.GetProperty("eligible").GetBoolean().Should().BeFalse();
        second.GetProperty("reason").GetString().Should().Contain("already exists");

        await using var conn = await _pool.OpenAsync();
        var count = await conn.ExecuteScalarAsync<long>(
            "SELECT COUNT(*) FROM returns WHERE order_id = @o", new { o = _deliveredOrderId }
        );
        count.Should().Be(1);
    }

    [Fact]
    public async Task InitiateReturn_RejectsAnUnknownRefundMethod()
    {
        var result = Json(await Returns().InitiateReturn(_deliveredOrderId.ToString(), "Faulty", "crypto"));

        result.GetProperty("error").GetString().Should().Contain("refund_method");
    }

    [Fact]
    public async Task ProcessRefund_MarksItRefundedOnce()
    {
        var created = Json(await Returns().InitiateReturn(_deliveredOrderId.ToString(), "Faulty"));
        var returnId = created.GetProperty("return_id").GetString()!;

        var first = Json(await Returns().ProcessRefund(returnId));
        first.GetProperty("status").GetString().Should().Be("refunded");

        // Guarded inside the UPDATE, so a repeat can't refund again — the same
        // claim-before-acting shape #28 established for HITL approvals.
        var second = Json(await Returns().ProcessRefund(returnId));
        second.GetProperty("error").GetString().Should().Contain("already refunded");
    }

    /// <summary>
    /// The gate is what stands between an agent and someone's money, so this
    /// asserts the registration rather than trusting it was remembered.
    /// </summary>
    [Fact]
    public void TheDestructiveReturnTools_AreHitlGated()
    {
        HitlGate.GatedTools.Should().Contain("InitiateReturn");
        HitlGate.GatedTools.Should().Contain("ProcessRefund");
    }

    // ─────────────────────── loyalty ───────────────────────

    [Fact]
    public async Task GetLoyaltyTier_ReturnsTheTierAndItsBenefits()
    {
        var result = Json(await Loyalty().GetLoyaltyTier());

        result.GetProperty("tier").GetString().Should().Be("gold");
        result.GetProperty("discount_pct").GetDecimal().Should().Be(10m);
        result.GetProperty("priority_support").GetBoolean().Should().BeTrue();
    }

    [Fact]
    public async Task CalculateLoyaltyDiscount_AppliesTheTierPercentage()
    {
        var result = Json(await Loyalty().CalculateLoyaltyDiscount(200m));

        result.GetProperty("discount_amount").GetDecimal().Should().Be(20m);
        result.GetProperty("total_after_discount").GetDecimal().Should().Be(180m);
        result.GetProperty("free_shipping").GetBoolean()
            .Should().BeTrue("gold's threshold is 0, i.e. always free");
    }

    [Fact]
    public async Task GetLoyaltyBenefits_ComparesEveryTierLowestFirst()
    {
        var result = Json(await Loyalty().GetLoyaltyBenefits());

        result.GetArrayLength().Should().Be(2);
        result[0].GetProperty("tier").GetString().Should().Be("bronze");
        result[1].GetProperty("tier").GetString().Should().Be("gold");
    }
}
