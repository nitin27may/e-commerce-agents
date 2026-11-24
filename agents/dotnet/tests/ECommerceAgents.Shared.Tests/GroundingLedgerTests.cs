using System.Text.Json;
using System.Text.Json.Nodes;
using Dapper;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Grounding;
using ECommerceAgents.Shared.Tools;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// The per-request ledger (twin of Python's <c>tests/test_grounding_ledger.py</c>). The shapes the shared vector
/// file pins are in <see cref="GroundingVectorTests"/>; these cover what is .NET's own: a tool returns a record or
/// an anonymous object rather than a dict, so keys arrive as <c>OrderId</c>, <c>orderId</c> or <c>order_id</c>.
/// </summary>
public sealed class GroundingLedgerTests
{
    private const string ProductId = "0fd372fa-ecb2-4db0-bb71-8628a784ced9";

    private sealed record PascalOrder(string OrderId, string Status, decimal Total, string? TrackingNumber, List<object> Items);

    private sealed record PascalProduct(string Id, string Name, decimal Price, decimal? OriginalPrice, string? ImageUrl);

    [Fact]
    public void Record_ReadsAPascalCaseRecordTheWayAToolReturns()
    {
        var ledger = new GroundingLedger();

        ledger.Record(new PascalOrder("1a2b3c4d-5e6f-7890-abcd-ef0123456789", "shipped", 199.99m, "TRK1", []));

        var order = ledger.Orders["1a2b3c4d-5e6f-7890-abcd-ef0123456789"];
        order.Tracking.Should().Be("TRK1");
        order.Total.Should().Be(199.99m);
    }

    [Fact]
    public void Record_ReadsAListOfRecords_AndOriginalPrice()
    {
        var ledger = new GroundingLedger();

        ledger.Record(new List<PascalProduct>
        {
            new(ProductId, "Widget", 299.99m, 349.99m, "u"),
            new("22222222-2222-2222-2222-222222222222", "B", 2m, null, null),
        });

        ledger.Products.Should().HaveCount(2);
        ledger.Products[ProductId].ImageUrl.Should().Be("u");
        ledger.KnownAmounts.Should().Contain(349.99m);
    }

    [Fact]
    public void Record_ReadsCamelCaseSnakeCaseAndAnonymousObjectsAlike()
    {
        var camel = JsonSerializer.SerializeToElement(
            new { orderId = "o1", status = "shipped", total = 10m }, new JsonSerializerOptions(JsonSerializerDefaults.Web));
        var ledger = new GroundingLedger();

        ledger.Record(camel);
        ledger.Record(new { order_id = "o2", status = "placed", total = 20m });

        ledger.Orders.Keys.Should().BeEquivalentTo(["o1", "o2"]);
    }

    [Fact]
    public void Record_ParsesAJsonStringResult_AndIgnoresPlainText()
    {
        var ledger = new GroundingLedger();

        ledger.Record($"{{\"id\":\"{ProductId}\",\"name\":\"W\",\"price\":5}}");
        ledger.Record("just some prose with $99.00 in it");

        ledger.Products.Should().ContainKey(ProductId);
        ledger.KnownAmounts.Should().NotContain(99m);
    }

    [Fact]
    public void Record_IgnoresNullAndScalars_AndNeverThrows()
    {
        var ledger = new GroundingLedger();

        ledger.Record(null);
        ledger.Record(42);
        ledger.Record(new object());
        ledger.Record(JsonNode.Parse("[1, 2, null]"));

        ledger.Products.Should().BeEmpty();
        ledger.KnownAmounts.Should().BeEmpty();
    }

    [Fact]
    public void Record_ReadsANumericStringAmount_LikePythonsFloat()
    {
        var ledger = new GroundingLedger();

        ledger.Record(new { current_price = "12.50" });

        ledger.KnownAmounts.Should().Contain(12.5m);
    }

    [Fact]
    public void Record_SurvivesConcurrentToolCalls()
    {
        var ledger = new GroundingLedger();

        Parallel.For(0, 200, i => ledger.Record(new { id = $"p{i}", name = "n", price = (decimal)i }));

        ledger.Products.Should().HaveCount(200);
    }

    [Fact]
    public void NoteToolArguments_ReadsAnAIFunctionArgumentsDictionary()
    {
        var ledger = new GroundingLedger();
        var arguments = new Dictionary<string, object?>
        {
            ["cart_total"] = JsonDocument.Parse("80.0").RootElement,
            ["max_price"] = 14.99,
            ["note"] = "under $20",
        };

        ledger.NoteToolArguments(arguments);

        ledger.UserAmounts.Should().BeEquivalentTo([80m, 14.99m, 20m]);
    }

    [Fact]
    public void ARequestScope_StartsWithAnEmptyLedger_AndRestoresTheOuterOneOnExit()
    {
        RequestContext.CurrentGroundingLedger.Should().BeNull();
        using (RequestContext.Scope("u@example.com", "customer", ""))
        {
            var outer = RequestContext.CurrentGroundingLedger;
            outer.Should().NotBeNull();
            outer!.NoteUserText("$5.00");
            using (RequestContext.Scope("u@example.com", "customer", ""))
            {
                RequestContext.CurrentGroundingLedger.Should().NotBeSameAs(outer);
                RequestContext.CurrentGroundingLedger!.UserAmounts.Should().BeEmpty();
            }

            RequestContext.CurrentGroundingLedger.Should().BeSameAs(outer);
        }
    }

    [Fact]
    public async Task ALedgerIsSharedByReference_SoARecordMadeInAChildTaskIsSeenByTheCaller()
    {
        using var scope = RequestContext.Scope("u@example.com", "customer", "");

        await Task.Run(() => RequestContext.CurrentGroundingLedger!.Record(new { total_savings = 162.44m }));

        RequestContext.CurrentGroundingLedger!.KnownAmounts.Should().Contain(162.44m);
    }
}

/// <summary>
/// The ledger over the real tools' own output (not hand-written JSON): the tools run against a real Postgres and
/// what they return is recorded as the pipeline stage would record it.
/// </summary>
[Collection(nameof(LocalPostgresCollection))]
public sealed class GroundingLedgerRealToolTests(PostgresFixture pg) : IAsyncLifetime
{
    private const string Email = "ledger@example.com";
    private DatabasePool _pool = null!;
    private Guid _productId;

    public async Task InitializeAsync()
    {
        _pool = new DatabasePool(new AgentSettings { DatabaseUrl = pg.ConnectionString });
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            "TRUNCATE price_history, order_items, orders, products, loyalty_tiers, users RESTART IDENTITY CASCADE");
        await conn.ExecuteAsync(
            @"INSERT INTO loyalty_tiers (name, min_spend, discount_pct, free_shipping_threshold, priority_support)
              VALUES ('bronze', 0, 0, 75, false), ('silver', 1000, 5, 50, false)");
        await conn.ExecuteAsync(
            "INSERT INTO users (email, password_hash, name, role, loyalty_tier, total_spend) VALUES (@Email, 'x', 'L', 'customer', 'bronze', 600)",
            new { Email });
        _productId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO products (name, description, category, brand, price)
              VALUES ('Widget', 'd', 'Electronics', 'Acme', 299.99) RETURNING id");
        await conn.ExecuteAsync(
            @"INSERT INTO price_history (product_id, price, recorded_at)
              VALUES (@id, 310.00, NOW() - INTERVAL '3 days'), (@id, 260.00, NOW() - INTERVAL '1 day')",
            new { id = _productId });
    }

    public async Task DisposeAsync() => await _pool.DisposeAsync();

    [Fact]
    public async Task GetPriceHistory_ItsAggregatesAreKnownAmounts()
    {
        var result = await new PriceHistoryTools(_pool).GetPriceHistory(_productId.ToString(), 30);
        var ledger = new GroundingLedger();

        ledger.Record(result);

        ledger.KnownAmounts.Should().Contain([299.99m, 285.00m, 260.00m, 310.00m]);
        ledger.KnownAmounts.Should().NotContain(30m, "the period in days is not an amount");
    }

    [Fact]
    public async Task GetLoyaltyTier_RecordsSpendAndThresholds()
    {
        using var scope = RequestContext.Scope(Email, "customer", "");
        var result = await new LoyaltyTools(_pool).GetLoyaltyTier();
        var ledger = new GroundingLedger();

        ledger.Record(result);

        ledger.KnownAmounts.Should().Contain([600m, 75m]);
    }
}
