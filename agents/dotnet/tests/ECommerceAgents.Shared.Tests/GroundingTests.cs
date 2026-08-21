using Dapper;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Grounding;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// Server-side grounding (#33 PR 7) — the last parity gate gap. .NET had no
/// grounding at all, so the fact-check badge never rendered on either the
/// authed chat or the anonymous storefront assistant.
/// </summary>
public sealed class ClaimExtractorTests
{
    [Fact]
    public void Extract_PullsProductClaimsOutOfACardFence()
    {
        var text = """
            Here's a good option:

            ```product
            {"id": "dc9e2baa-8182-58f8-9df1-97d049426ba1", "name": "Sony WH-1000XM5", "price": 299.99}
            ```
            """;

        var claims = ClaimExtractor.Extract(text);

        claims.Products.Should().HaveCount(1);
        claims.Products[0].Name.Should().Be("Sony WH-1000XM5");
        claims.Products[0].Price.Should().Be(299.99m);
    }

    [Fact]
    public void Extract_HandlesAProductsArray()
    {
        var text = """
            ```products
            [{"id": "dc9e2baa-8182-58f8-9df1-97d049426ba1", "price": 10},
             {"id": "035ae219-49be-59e2-a71e-94f06ca0fec9", "price": 20}]
            ```
            """;

        ClaimExtractor.Extract(text).Products.Should().HaveCount(2);
    }

    /// <summary>
    /// A card's own id and price must not also be counted as prose claims —
    /// otherwise every card would be verified twice and the totals would lie.
    /// </summary>
    [Fact]
    public void Extract_DoesNotDoubleCountACardsOwnIdAsProse()
    {
        var text = """
            ```product
            {"id": "dc9e2baa-8182-58f8-9df1-97d049426ba1", "price": 299.99}
            ```
            """;

        var claims = ClaimExtractor.Extract(text);

        claims.Products.Should().HaveCount(1);
        claims.BareIds.Should().BeEmpty();
        claims.Amounts.Should().BeEmpty("the $299.99 lives inside the fence, not in prose");
    }

    [Fact]
    public void Extract_FindsBareIdsAndAmountsMentionedInProse()
    {
        var text = "Order dc9e2baa-8182-58f8-9df1-97d049426ba1 shipped for $42.50 via TRK99XZ.";

        var claims = ClaimExtractor.Extract(text);

        claims.BareIds.Should().ContainSingle();
        claims.Amounts.Should().Contain(42.50m);
        claims.TrackingNumbers.Should().Contain("TRK99XZ");
    }

    /// <summary>
    /// A model that emits a broken card has already failed the UI contract.
    /// That's a rendering bug, not a reason for grounding to throw.
    /// </summary>
    [Fact]
    public void Extract_SkipsMalformedJsonRatherThanThrowing()
    {
        var text = "```product\n{not json at all\n```";

        ClaimExtractor.Extract(text).Total.Should().Be(0);
    }

    [Fact]
    public void Extract_EmptyOrPlainText_ClaimsNothing()
    {
        ClaimExtractor.Extract(null).Total.Should().Be(0);
        ClaimExtractor.Extract("Hi Alice! How can I help?").Total.Should().Be(0);
    }
}

[Collection(nameof(LocalPostgresCollection))]
public sealed class GroundingVerifierTests : IAsyncLifetime
{
    private readonly PostgresFixture _pg;
    private DatabasePool _pool = null!;
    private Guid _productId;

    public GroundingVerifierTests(PostgresFixture pg) => _pg = pg;

    public async Task InitializeAsync()
    {
        _pool = new DatabasePool(new AgentSettings { DatabaseUrl = _pg.ConnectionString });
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync("TRUNCATE order_items, orders, products, users RESTART IDENTITY CASCADE");
        _productId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO products (name, description, category, brand, price)
              VALUES ('Real Product', 'd', 'Electronics', 'Acme', 100.00) RETURNING id"
        );
    }

    public async Task DisposeAsync() => await _pool.DisposeAsync();

    private GroundingVerifier Verifier() => new(_pool);

    [Fact]
    public async Task Verify_AProductThatExistsAtTheQuotedPrice_IsVerified()
    {
        var claims = ClaimExtractor.Extract($$"""
            ```product
            {"id": "{{_productId}}", "price": 100.00}
            ```
            """);

        var report = await Verifier().VerifyAsync(claims);

        report.Verified.Should().Be(1);
        report.Unverified.Should().Be(0);
        report.Verdicts[0].Source.Should().Be("db");
    }

    /// <summary>
    /// The failure this whole subsystem exists for: a well-formed card naming
    /// a product that doesn't exist. It renders perfectly and 404s downstream.
    /// </summary>
    [Fact]
    public async Task Verify_AFabricatedProductId_IsNotFound()
    {
        var claims = ClaimExtractor.Extract($$"""
            ```product
            {"id": "{{Guid.NewGuid()}}", "price": 100.00}
            ```
            """);

        var report = await Verifier().VerifyAsync(claims);

        report.Verified.Should().Be(0);
        report.Verdicts[0].Status.Should().Be("not_found");
    }

    /// <summary>
    /// A real product at the wrong price is more misleading than a fake one,
    /// because everything about it looks right.
    /// </summary>
    [Fact]
    public async Task Verify_ARealProductAtTheWrongPrice_IsAPriceMismatch()
    {
        var claims = ClaimExtractor.Extract($$"""
            ```product
            {"id": "{{_productId}}", "price": 9.99}
            ```
            """);

        var report = await Verifier().VerifyAsync(claims);

        report.Verdicts[0].Status.Should().Be("price_mismatch");
        report.Verdicts[0].Detail.Should().Contain("100.00");
    }

    [Fact]
    public async Task Verify_ProseFiguresCitingNothing_AreUnverifiableNotDropped()
    {
        // No card in this answer, so there is no cited row to check against.
        // Saying so beats a verified count that quietly excludes the figure.
        var claims = ClaimExtractor.Extract("It cost $42.50 and shipped via TRK99XZ.");

        var report = await Verifier().VerifyAsync(claims);

        report.Total.Should().Be(2);
        report.Verdicts.Should().OnlyContain(v => v.Status == "unverifiable");
    }

    /// <summary>
    /// The shape almost every real answer takes: a card, and the same price
    /// restated in the prose above it. Python resolves that from its ledger;
    /// leaving it permanently "unverifiable" here would make the .NET badge
    /// read worse than Python's for a byte-identical answer.
    /// </summary>
    [Fact]
    public async Task Verify_AProsePriceRestatingACitedCard_IsVerified()
    {
        var claims = ClaimExtractor.Extract($$"""
            This one is $100.00:

            ```product
            {"id": "{{_productId}}", "price": 100.00}
            ```
            """);

        var report = await Verifier().VerifyAsync(claims);

        report.Total.Should().Be(2);
        report.Verified.Should().Be(2);
        report.Verdicts.Single(v => v.Type == "amount").Source.Should().Be("db");
    }

    /// <summary>
    /// A figure that matches no cited row must not be waved through just
    /// because the answer happened to contain a valid card.
    /// </summary>
    [Fact]
    public async Task Verify_AProsePriceMatchingNoCitedRow_StaysUnverifiable()
    {
        var claims = ClaimExtractor.Extract($$"""
            Normally $999.99, on sale now:

            ```product
            {"id": "{{_productId}}", "price": 100.00}
            ```
            """);

        var report = await Verifier().VerifyAsync(claims);

        report.Verdicts.Single(v => v.Type == "amount").Status.Should().Be("unverifiable");
    }

    [Fact]
    public async Task Verify_ATrackingNumberOnACitedOrder_IsVerified()
    {
        Guid orderId;
        await using (var conn = await _pool.OpenAsync())
        {
            var userId = await conn.ExecuteScalarAsync<Guid>(
                @"INSERT INTO users (email, password_hash, name, role)
                  VALUES ('grounding@example.com', 'x', 'G', 'customer') RETURNING id"
            );
            orderId = await conn.ExecuteScalarAsync<Guid>(
                @"INSERT INTO orders (user_id, status, total, tracking_number, shipping_address)
                  VALUES (@userId, 'shipped', 55.00, 'TRK99XZ', '{}'::jsonb) RETURNING id",
                new { userId }
            );
        }

        var claims = ClaimExtractor.Extract($$"""
            Shipped via TRK99XZ:

            ```order
            {"id": "{{orderId}}", "status": "shipped", "total": 55.00}
            ```
            """);

        var report = await Verifier().VerifyAsync(claims);

        report.Verdicts.Single(v => v.Type == "tracking").Status.Should().Be("verified");
    }

    [Fact]
    public async Task Verify_NoClaims_ProducesAnEmptyReport()
    {
        var report = await Verifier().VerifyAsync(ExtractedClaims.Empty);

        report.Total.Should().Be(0);
    }

    [Fact]
    public async Task ToWire_UsesTheSnakeCaseShapeTheClientParses()
    {
        var claims = ClaimExtractor.Extract($$"""
            ```product
            {"id": "{{_productId}}", "price": 100.00}
            ```
            """);

        var wire = System.Text.Json.JsonSerializer.SerializeToElement(
            (await Verifier().VerifyAsync(claims)).ToWire()
        );

        // The badge reads verified/unverified directly; camelCase here would
        // render "undefined facts verified against the database".
        wire.GetProperty("verified").GetInt32().Should().Be(1);
        wire.GetProperty("unverified").GetInt32().Should().Be(0);
        wire.GetProperty("claims")[0].GetProperty("status").GetString().Should().Be("verified");
    }
}
