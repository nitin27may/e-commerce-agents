using Dapper;
using ECommerceAgents.PricingPromotions.Tools;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using Xunit;

namespace ECommerceAgents.PricingPromotions.Tests;

[CollectionDefinition(nameof(LocalPostgresCollection))]
public sealed class LocalPostgresCollection : ICollectionFixture<PostgresFixture> { }

[Collection(nameof(LocalPostgresCollection))]
public sealed class PricingToolsTests : IAsyncLifetime
{
    private readonly PostgresFixture _pg;
    private DatabasePool _pool = null!;
    private PricingTools _tools = null!;
    private const string Email = "tester@example.com";

    private Guid _productElectronicsId;
    private Guid _productClothingId;

    public PricingToolsTests(PostgresFixture pg)
    {
        _pg = pg;
        RequestContext.CurrentUserEmail = Email;
    }

    public async Task InitializeAsync()
    {
        var settings = new AgentSettings { DatabaseUrl = _pg.ConnectionString };
        _pool = new DatabasePool(settings);
        _tools = new PricingTools(_pool);
        RequestContext.CurrentUserEmail = Email;
        await SeedAsync();
    }

    public async Task DisposeAsync()
    {
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            "TRUNCATE promotions, coupons, loyalty_tiers, products, users RESTART IDENTITY CASCADE"
        );
        RequestContext.CurrentUserEmail = "";
        await _pool.DisposeAsync();
    }

    private void EnsureUserScope() => RequestContext.CurrentUserEmail = Email;

    // ─────────────────────── validate_coupon ──────────────────

    [Fact]
    public async Task ValidateCoupon_RejectsUnknownCode()
    {
        EnsureUserScope();
        var result = await _tools.ValidateCoupon("NOPE", 100m);
        result.Valid.Should().BeFalse();
        result.Error.Should().Contain("not found");
    }

    [Fact]
    public async Task ValidateCoupon_RejectsExpiredCoupon()
    {
        EnsureUserScope();
        var result = await _tools.ValidateCoupon("EXPIRED10", 100m);
        result.Valid.Should().BeFalse();
        result.Error.Should().Contain("expired");
    }

    [Fact]
    public async Task ValidateCoupon_RejectsBelowMinSpend()
    {
        EnsureUserScope();
        var result = await _tools.ValidateCoupon("MIN50", 25m);
        result.Valid.Should().BeFalse();
        result.Error.Should().Contain("Minimum spend");
    }

    [Fact]
    public async Task ValidateCoupon_RejectsCategoryMismatch()
    {
        EnsureUserScope();
        var result = await _tools.ValidateCoupon("ELEC15", 100m, category: "Clothing");
        result.Valid.Should().BeFalse();
        result.Error.Should().Contain("Electronics");
    }

    [Fact]
    public async Task ValidateCoupon_HappyPath_ComputesPercentageDiscount()
    {
        EnsureUserScope();
        var result = await _tools.ValidateCoupon("SAVE10", 200m);
        result.Valid.Should().BeTrue();
        result.DiscountAmount.Should().Be(20m);
        result.NewTotal.Should().Be(180m);
    }

    [Fact]
    public async Task ValidateCoupon_AppliesMaxDiscountCap()
    {
        EnsureUserScope();
        // SAVE10MAX is 10% with max_discount=15. On a $1000 cart 10% would
        // be $100, but it should be capped at $15.
        var result = await _tools.ValidateCoupon("SAVE10MAX", 1_000m);
        result.Valid.Should().BeTrue();
        result.DiscountAmount.Should().Be(15m);
    }

    [Fact]
    public async Task ValidateCoupon_FixedAmountDoesNotExceedCart()
    {
        EnsureUserScope();
        // FLAT100 is fixed $100; on a $40 cart the discount caps at $40.
        var result = await _tools.ValidateCoupon("FLAT100", 40m);
        result.Valid.Should().BeTrue();
        result.DiscountAmount.Should().Be(40m);
        result.NewTotal.Should().Be(0m);
    }

    [Fact]
    public async Task ValidateCoupon_RejectsForOtherUser()
    {
        EnsureUserScope();
        var result = await _tools.ValidateCoupon("VIPONLY", 200m);
        result.Valid.Should().BeFalse();
        result.Error.Should().Contain("specific user");
    }

    // ─────────────────────── get_active_deals ─────────────────

    [Fact]
    public async Task GetActiveDeals_ReturnsCouponsAndPromotions()
    {
        EnsureUserScope();
        var deals = await _tools.GetActiveDeals();
        deals.TotalDeals.Should().BeGreaterThan(0);
        // Expired and user-specific coupons must NOT appear.
        deals.Coupons.Should().NotContain(c => c.Code == "EXPIRED10");
        deals.Coupons.Should().NotContain(c => c.Code == "VIPONLY");
        deals.Coupons.Should().Contain(c => c.Code == "SAVE10");
    }

    // ─────────────────────── optimize_cart ────────────────────

    [Fact]
    public async Task OptimizeCart_RejectsUnknownProduct()
    {
        EnsureUserScope();
        var result = await _tools.OptimizeCart([
            new CartItemInput(Guid.NewGuid().ToString(), 1)
        ]);
        result.Error.Should().Contain("Product not found");
    }

    [Fact]
    public async Task OptimizeCart_PicksBestCouponAndAppliesLoyalty()
    {
        EnsureUserScope();
        var result = await _tools.OptimizeCart([
            new CartItemInput(_productElectronicsId.ToString(), 1)
        ]);
        result.Error.Should().BeNull();
        result.OriginalTotal.Should().Be(200m);
        // Best coupon is SAVE10 → 20.0; loyalty silver = 5% on 200 = 10.0
        result.Savings!.Should().Contain(s => s.Type == "coupon");
        result.Savings!.Should().Contain(s => s.Type == "loyalty_discount");
        result.TotalSavings.Should().BeGreaterThan(0m);
        result.FinalTotal.Should().BeLessThan(result.OriginalTotal!.Value);
    }

    // ─────────────────────── check_bundle_eligibility ─────────

    [Fact]
    public async Task CheckBundleEligibility_NoMatchingPromos()
    {
        EnsureUserScope();
        // Just one product → no bundle promo qualifies.
        var result = await _tools.CheckBundleEligibility([_productElectronicsId.ToString()]);
        result.Eligible.Should().BeFalse();
        result.BundleDeals.Should().BeEmpty();
    }

    [Fact]
    public async Task CheckBundleEligibility_MatchesByProductIds()
    {
        EnsureUserScope();
        // Bundle promo seeded below requires both Electronics + Clothing.
        var result = await _tools.CheckBundleEligibility([
            _productElectronicsId.ToString(),
            _productClothingId.ToString(),
        ]);
        result.Eligible.Should().BeTrue();
        result.BundleDeals.Should().HaveCount(1);
        result.BundleDeals[0].DiscountPct.Should().Be(20m);
    }

    [Fact]
    public async Task CheckBundleEligibility_EmptyOnAllInvalidIds()
    {
        EnsureUserScope();
        var result = await _tools.CheckBundleEligibility(["not-a-uuid"]);
        result.Eligible.Should().BeFalse();
        result.Error.Should().Contain("No valid products");
    }

    // ─────────────────────── seed ─────────────────────────────

    private async Task SeedAsync()
    {
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            "TRUNCATE promotions, coupons, loyalty_tiers, products, users RESTART IDENTITY CASCADE"
        );

        // User on the silver tier (5% loyalty).
        await conn.ExecuteAsync(
            @"INSERT INTO users (email, password_hash, name, role, loyalty_tier)
              VALUES (@email, 'x', 'Tester', 'customer', 'silver')",
            new { email = Email }
        );

        await conn.ExecuteAsync(
            @"INSERT INTO loyalty_tiers (name, min_spend, discount_pct)
              VALUES ('bronze', 0, 0),
                     ('silver', 100, 5),
                     ('gold', 1000, 10)"
        );

        _productElectronicsId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO products (name, description, category, brand, price)
              VALUES ('Headphones', 'Sample', 'Electronics', 'X', 200)
              RETURNING id"
        );
        _productClothingId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO products (name, description, category, brand, price)
              VALUES ('T-Shirt', 'Sample', 'Clothing', 'Y', 30)
              RETURNING id"
        );

        await conn.ExecuteAsync(
            @"INSERT INTO coupons (code, description, discount_type, discount_value, min_spend, max_discount, valid_from, valid_until, applicable_categories, user_specific_email, is_active)
              VALUES
                ('SAVE10',     '10% off',           'percentage', 10, 0,   NULL, NOW() - INTERVAL '1 day', NOW() + INTERVAL '30 days', NULL, NULL, TRUE),
                ('SAVE10MAX',  '10% off, max $15',  'percentage', 10, 0,   15,   NOW() - INTERVAL '1 day', NOW() + INTERVAL '30 days', NULL, NULL, TRUE),
                ('FLAT100',    '$100 off',          'fixed',      100, 0,  NULL, NOW() - INTERVAL '1 day', NOW() + INTERVAL '30 days', NULL, NULL, TRUE),
                ('MIN50',      '15% off above $50', 'percentage', 15, 50,  NULL, NOW() - INTERVAL '1 day', NOW() + INTERVAL '30 days', NULL, NULL, TRUE),
                ('ELEC15',     'Electronics 15%',   'percentage', 15, 0,   NULL, NOW() - INTERVAL '1 day', NOW() + INTERVAL '30 days', ARRAY['Electronics'], NULL, TRUE),
                ('VIPONLY',    'VIP only',          'percentage', 50, 0,   NULL, NOW() - INTERVAL '1 day', NOW() + INTERVAL '30 days', NULL, 'vip@example.com', TRUE),
                ('EXPIRED10',  'expired',           'percentage', 10, 0,   NULL, NOW() - INTERVAL '60 days', NOW() - INTERVAL '1 day', NULL, NULL, TRUE)"
        );

        // Bundle promo requiring both products.
        var rules = $"{{\"product_ids\":[\"{_productElectronicsId}\",\"{_productClothingId}\"],\"discount_pct\":20}}";
        await conn.ExecuteAsync(
            @"INSERT INTO promotions (name, type, rules, start_date, end_date, is_active)
              VALUES ('Spring bundle', 'bundle', @rules::jsonb, NOW() - INTERVAL '1 day', NOW() + INTERVAL '30 days', TRUE)",
            new { rules }
        );
    }
}
