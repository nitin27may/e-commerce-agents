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
/// A gated call that can never succeed is refused in words BEFORE it parks (<see cref="ParkPrechecks"/>) — the .NET mirror of
/// Python's <c>tests/test_park_prechecks.py</c>. Runs <see cref="HitlGate.TryGateAsync"/> against a real Postgres testcontainer.
/// A refused call writes no <c>tool_approval_requests</c> or <c>approvals</c> row; an allowed one still parks exactly one.
/// </summary>
[Collection(nameof(LocalPostgresCollection))]
public sealed class ParkPrecheckTests : IAsyncLifetime
{
    private const string Customer = "jack.anderson@gmail.com";
    private const string Other = "someone.else@example.com";
    private const string Seller = "seller@example.com";
    private const string NotSubmitted = "I have not submitted anything for approval";

    private static readonly string[] CustomerSeller = ["customer", "seller"];
    private static readonly string[] SellerAdmin = ["seller", "admin"];

    private readonly PostgresFixture _pg;
    private DatabasePool _pool = null!;
    private HitlGate _gate = null!;

    public ParkPrecheckTests(PostgresFixture pg)
    {
        _pg = pg;
        RequestContext.CurrentUserEmail = Customer;
        RequestContext.CurrentUserRole = "customer";
        RequestContext.CurrentSessionId = "";
    }

    public async Task InitializeAsync()
    {
        var settings = new AgentSettings { DatabaseUrl = _pg.ConnectionString, HitlEnabled = true };
        _pool = new DatabasePool(settings);
        _gate = new HitlGate(_pool, settings);
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            "TRUNCATE tool_approval_requests, approvals, order_status_history, returns, order_items, orders, warehouse_inventory, warehouses, products, users RESTART IDENTITY CASCADE");
        RequestContext.CurrentUserEmail = Customer;
        RequestContext.CurrentUserRole = "customer";
    }

    public async Task DisposeAsync()
    {
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            "TRUNCATE tool_approval_requests, approvals, order_status_history, returns, order_items, orders, warehouse_inventory, warehouses, products, users RESTART IDENTITY CASCADE");
        await _pool.DisposeAsync();
    }

    // ───────────────────────── seeding ─────────────────────────

    private async Task<Guid> UserAsync(string email, string role = "customer")
    {
        await using var conn = await _pool.OpenAsync();
        var existing = await conn.ExecuteScalarAsync<Guid?>("SELECT id FROM users WHERE email = @email", new { email });
        return existing ?? await conn.ExecuteScalarAsync<Guid>(
            "INSERT INTO users (email, password_hash, name, role) VALUES (@email, 'x', 'T', @role) RETURNING id", new { email, role });
    }

    private async Task<Guid> OrderAsync(string email, string status, int? deliveredDaysAgo = null)
    {
        var userId = await UserAsync(email);
        await using var conn = await _pool.OpenAsync();
        var id = await conn.ExecuteScalarAsync<Guid>(
            "INSERT INTO orders (user_id, status, total, shipping_address) VALUES (@userId, @status, 25, '{}'::jsonb) RETURNING id",
            new { userId, status });
        if (deliveredDaysAgo is { } days)
        {
            await conn.ExecuteAsync(
                "INSERT INTO order_status_history (order_id, status, timestamp) VALUES (@id, 'delivered', NOW() - make_interval(days => @days))",
                new { id, days });
        }

        return id;
    }

    private async Task<Guid> ReturnAsync(Guid orderId, string email, string status)
    {
        var userId = await UserAsync(email);
        await using var conn = await _pool.OpenAsync();
        return await conn.ExecuteScalarAsync<Guid>(
            "INSERT INTO returns (order_id, user_id, reason, status, refund_amount) VALUES (@orderId, @userId, 'r', @status, 25) RETURNING id",
            new { orderId, userId, status });
    }

    private async Task<Guid> ProductAsync(int stock)
    {
        var sellerId = await UserAsync(Seller, "seller");
        await using var conn = await _pool.OpenAsync();
        var productId = await conn.ExecuteScalarAsync<Guid>(
            "INSERT INTO products (name, description, category, price, seller_id) VALUES ('Widget', 'd', 'Home', 9.99, @sellerId) RETURNING id",
            new { sellerId });
        var warehouseId = await conn.ExecuteScalarAsync<Guid>(
            "INSERT INTO warehouses (name, location, region) VALUES ('E', 'X', 'east') RETURNING id");
        await conn.ExecuteAsync(
            "INSERT INTO warehouse_inventory (warehouse_id, product_id, quantity) VALUES (@warehouseId, @productId, @stock)",
            new { warehouseId, productId, stock });
        return productId;
    }

    private async Task<(long Requests, long Approvals)> RowsAsync()
    {
        await using var conn = await _pool.OpenAsync();
        return (
            await conn.ExecuteScalarAsync<long>("SELECT COUNT(*) FROM tool_approval_requests"),
            await conn.ExecuteScalarAsync<long>("SELECT COUNT(*) FROM approvals"));
    }

    private Task<object?> GateAsync(string tool, object input, IReadOnlyList<string>? roles = null) =>
        _gate.TryGateAsync(tool, "order-management", input, roles);

    private static string Message(object? result)
    {
        result.Should().NotBeNull();
        var json = JsonSerializer.SerializeToElement(result);
        return json.GetProperty("message").GetString()!;
    }

    private static string Status(object? result) =>
        JsonSerializer.SerializeToElement(result).GetProperty("status").GetString()!;

    private static void Refused(object? result, params string[] needles)
    {
        Status(result).Should().Be("refused");
        var text = Message(result);
        text.Should().Contain(NotSubmitted);
        text.Should().NotContain("submitted for manager approval");
        foreach (var needle in needles)
        {
            text.Should().Contain(needle);
        }
    }

    private static void Parked(object? result) => Status(result).Should().Be("pending_approval");

    // ───────────────────────── cancel_order ─────────────────────────

    [Fact]
    public async Task Cancel_AShippedOrder_IsRefusedAndWritesNoRow()
    {
        var orderId = await OrderAsync(Customer, "shipped");

        var result = await GateAsync("cancel_order", new { orderId = orderId.ToString(), reason = "changed my mind" }, CustomerSeller);

        Refused(result, $"Order {orderId} has already shipped", "can no longer be cancelled");
        (await RowsAsync()).Should().Be((0, 0));
    }

    [Fact]
    public async Task Cancel_APlacedOrder_StillParksExactlyOneRow()
    {
        var orderId = await OrderAsync(Customer, "placed");

        var result = await GateAsync("cancel_order", new { orderId = orderId.ToString(), reason = "changed my mind" }, CustomerSeller);

        Parked(result);
        (await RowsAsync()).Should().Be((1, 1));
    }

    [Fact]
    public async Task Cancel_PascalCasePipelineName_IsGatedToo()
    {
        // The pipeline reports the registered snake_case name; the gated set is PascalCase. Both must gate.
        var orderId = await OrderAsync(Customer, "shipped");

        var result = await GateAsync("CancelOrder", new { orderId = orderId.ToString(), reason = "x" }, CustomerSeller);

        Refused(result, "has already shipped");
    }

    [Fact]
    public async Task AnotherCustomersOrMissingOrder_GetsTheSameNeutralSentence()
    {
        var theirs = await OrderAsync(Other, "shipped");
        const string neutral = "I could not find that order on your account. I have not submitted anything for approval.";

        foreach (var (tool, input) in new (string, object)[]
        {
            ("cancel_order", new { orderId = theirs.ToString(), reason = "x" }),
            ("cancel_order", new { orderId = Guid.NewGuid().ToString(), reason = "x" }),
            ("cancel_order", new { orderId = "not-a-uuid", reason = "x" }),
            ("modify_order", new { orderId = theirs.ToString(), newAddress = new { } }),
            ("initiate_return", new { orderId = theirs.ToString(), reason = "x" }),
        })
        {
            var result = await GateAsync(tool, input, CustomerSeller);
            Message(result).Should().Be(neutral);
            Message(result).Should().NotContain("shipped").And.NotContain(theirs.ToString());
        }

        (await RowsAsync()).Should().Be((0, 0));
    }

    // ───────────────────────── modify_order ─────────────────────────

    [Fact]
    public async Task Modify_ADeliveredOrderIsRefused_AndAPlacedOneParks()
    {
        var delivered = await OrderAsync(Customer, "delivered", deliveredDaysAgo: 2);
        var address = new { street = "1 Main", city = "Austin", state = "TX", zip = "78701", country = "US" };

        var refused = await GateAsync("modify_order", new { orderId = delivered.ToString(), newAddress = address }, CustomerSeller);
        Refused(refused, $"Order {delivered} has already been delivered", "address can no longer be changed");
        (await RowsAsync()).Should().Be((0, 0));

        var placed = await OrderAsync(Customer, "placed");
        Parked(await GateAsync("modify_order", new { orderId = placed.ToString(), newAddress = address }, CustomerSeller));
        (await RowsAsync()).Should().Be((1, 1));
    }

    // ───────────────────────── initiate_return / process_refund ─────────────────────────

    [Fact]
    public async Task InitiateReturn_Prechecks()
    {
        var shipped = await OrderAsync(Customer, "shipped");
        Refused(await GateAsync("initiate_return", new { orderId = shipped.ToString(), reason = "x" }, CustomerSeller), "has already shipped");

        var expired = await OrderAsync(Customer, "delivered", deliveredDaysAgo: 45);
        Refused(
            await GateAsync("initiate_return", new { orderId = expired.ToString(), reason = "x" }, CustomerSeller),
            "outside the 30-day return window",
            "delivered 45 days ago");

        var returned = await OrderAsync(Customer, "delivered", deliveredDaysAgo: 3);
        await ReturnAsync(returned, Customer, "requested");
        Refused(await GateAsync("initiate_return", new { orderId = returned.ToString(), reason = "x" }, CustomerSeller), "return has already been started");
        (await RowsAsync()).Should().Be((0, 0));

        var ok = await OrderAsync(Customer, "delivered", deliveredDaysAgo: 3);
        Parked(await GateAsync("initiate_return", new { orderId = ok.ToString(), reason = "x" }, CustomerSeller));
        (await RowsAsync()).Should().Be((1, 1));
    }

    [Fact]
    public async Task ProcessRefund_Prechecks()
    {
        var done = await ReturnAsync(await OrderAsync(Customer, "returned"), Customer, "refunded");
        Refused(await GateAsync("process_refund", new { returnId = done.ToString() }, CustomerSeller), "has already been refunded");

        var denied = await ReturnAsync(await OrderAsync(Customer, "returned"), Customer, "denied");
        Refused(await GateAsync("process_refund", new { returnId = denied.ToString() }, CustomerSeller), "was denied and cannot be refunded");

        var theirs = await ReturnAsync(await OrderAsync(Other, "returned"), Other, "requested");
        foreach (var returnId in new[] { theirs.ToString(), Guid.NewGuid().ToString(), "nope" })
        {
            Message(await GateAsync("process_refund", new { returnId }, CustomerSeller))
                .Should().Be("I could not find that return on your account. I have not submitted anything for approval.");
        }

        (await RowsAsync()).Should().Be((0, 0));

        var open = await ReturnAsync(await OrderAsync(Customer, "returned"), Customer, "requested");
        Parked(await GateAsync("process_refund", new { returnId = open.ToString() }, CustomerSeller));
        (await RowsAsync()).Should().Be((1, 1));
    }

    // ───────────────────────── place_backorder ─────────────────────────

    [Fact]
    public async Task ACustomerBackorder_IsDeniedByRole_AndWritesNoRow()
    {
        var product = await ProductAsync(stock: 0);

        var result = await GateAsync("place_backorder", new { productId = product.ToString(), quantity = 5 }, SellerAdmin);

        Refused(result, "not permitted to place backorders", "admin, seller");
        (await RowsAsync()).Should().Be((0, 0));
    }

    [Fact]
    public async Task SellerBackorder_Prechecks()
    {
        var outOfStock = await ProductAsync(stock: 0);
        var inStock = await ProductAsync(stock: 7);
        RequestContext.CurrentUserEmail = Seller;
        RequestContext.CurrentUserRole = "seller";

        Refused(await GateAsync("place_backorder", new { productId = Guid.NewGuid().ToString(), quantity = 5 }, SellerAdmin), "could not find");
        Refused(await GateAsync("place_backorder", new { productId = inStock.ToString(), quantity = 5 }, SellerAdmin), "7 units available");
        Refused(await GateAsync("place_backorder", new { productId = outOfStock.ToString(), quantity = 0 }, SellerAdmin), "greater than zero");
        (await RowsAsync()).Should().Be((0, 0));

        Parked(await GateAsync("place_backorder", new { productId = outOfStock.ToString(), quantity = 5 }, SellerAdmin));
        (await RowsAsync()).Should().Be((1, 1));
    }

    [Fact]
    public async Task AnAdmin_PassesTheRoleGuard()
    {
        var product = await ProductAsync(stock: 0);
        RequestContext.CurrentUserEmail = "admin@example.com";
        RequestContext.CurrentUserRole = "admin";

        Parked(await GateAsync("place_backorder", new { productId = product.ToString(), quantity = 2 }, SellerAdmin));
    }

    [Fact]
    public async Task GuardrailsOff_SkipsTheRoleLayerOnly()
    {
        var product = await ProductAsync(stock: 0);
        var settings = new AgentSettings { DatabaseUrl = _pg.ConnectionString, HitlEnabled = true, GuardrailsEnabled = false };
        var gate = new HitlGate(_pool, settings);

        Parked(await gate.TryGateAsync("place_backorder", "inventory-fulfillment", new { productId = product.ToString(), quantity = 2 }, SellerAdmin));
    }

    // ───────────────────────── idempotency, fail-closed ─────────────────────────

    [Fact]
    public async Task NoIdentity_FailsClosedWithTheFixedSentence_AndWritesNothing()
    {
        var orderId = await OrderAsync(Customer, "placed");
        RequestContext.CurrentUserEmail = "";

        var result = await GateAsync("cancel_order", new { orderId = orderId.ToString(), reason = "x" }, CustomerSeller);

        Status(result).Should().Be("error");
        Message(result).Should().Be(ParkPrechecks.ParkFailedReply);
        Message(result).Should().Contain("no changes have been made");
        (await RowsAsync()).Should().Be((0, 0));
    }

    [Fact]
    public async Task ADatabaseError_FailsClosedWithTheFixedSentence()
    {
        var broken = new DatabasePool(new AgentSettings { DatabaseUrl = "postgresql://nouser:nopass@localhost:1/nonexistent" });
        var gate = new HitlGate(broken, new AgentSettings { HitlEnabled = true });

        var result = await gate.TryGateAsync("cancel_order", "order-management", new { orderId = Guid.NewGuid().ToString(), reason = "x" }, CustomerSeller);

        Status(result).Should().Be("error");
        Message(result).Should().Be(ParkPrechecks.ParkFailedReply);
        await broken.DisposeAsync();
    }

    // ───────────────────────── registry and shared rules ─────────────────────────

    [Fact]
    public void EveryGatedTool_HasAPrecheckRegistered()
    {
        var gated = HitlGate.GatedTools.Select(Approvals.ApprovalIntake.CanonicalToolName);

        ParkPrechecks.RegisteredTools.Should().Contain(gated);
    }

    [Fact]
    public void TheReturnTools_DeclareTheRolesTheGateReads()
    {
        var tools = new ReturnTools(_pool, new AgentSettings()).All().OfType<Microsoft.Extensions.AI.AIFunction>().ToDictionary(f => f.Name);

        HitlGate.RolesOf(tools["initiate_return"]).Should().BeEquivalentTo(CustomerSeller);
        HitlGate.RolesOf(tools["process_refund"]).Should().BeEquivalentTo(CustomerSeller);
        HitlGate.RolesOf(tools["get_return_status"]).Should().BeNull();
    }

    [Theory]
    [InlineData("placed", null)]
    [InlineData("confirmed", null)]
    [InlineData("shipped", "has already shipped")]
    [InlineData("delivered", "has already been delivered")]
    [InlineData("odd", "is in 'odd' status")]
    public void GateRules_CancelAndModify_ShareTheStatusReasons(string status, string? reason)
    {
        GateRules.CancelBlocker(status).Should().Be(reason);
        GateRules.ModifyBlocker(status).Should().Be(reason);
    }

    [Fact]
    public void GateRules_ReturnAndRefundRules()
    {
        GateRules.ReturnStatusBlocker("delivered").Should().BeNull();
        GateRules.ReturnStatusBlocker("shipped").Should().Be("has already shipped");
        GateRules.RefundBlocker("requested").Should().BeNull();
        GateRules.RefundBlocker("refunded").Should().Be("has already been refunded");
        GateRules.RefundBlocker("denied").Should().Be("was denied and cannot be refunded");
        GateRules.ReturnWindowExpired(null).Should().BeFalse();
        GateRules.ReturnWindowExpired(30).Should().BeFalse();
        GateRules.ReturnWindowExpired(31).Should().BeTrue();
    }
}
