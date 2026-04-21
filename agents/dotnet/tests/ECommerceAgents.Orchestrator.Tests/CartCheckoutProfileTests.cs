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

[Collection(nameof(LocalPostgresCollection))]
public sealed class CartCheckoutProfileTests : IAsyncLifetime
{
    private readonly PostgresFixture _pg;
    private DatabasePool _pool = null!;
    private const string Email = "cart@example.com";
    private Guid _userId;
    private Guid _productAId;
    private Guid _productBId;
    private Guid _warehouseId;

    public CartCheckoutProfileTests(PostgresFixture pg) => _pg = pg;

    public async Task InitializeAsync()
    {
        var settings = new AgentSettings { DatabaseUrl = _pg.ConnectionString };
        _pool = new DatabasePool(settings);
        await SeedAsync();
    }

    public async Task DisposeAsync()
    {
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            @"TRUNCATE order_status_history, order_items, returns, orders,
                       cart_items, carts, messages, conversations,
                       warehouse_inventory, warehouses, coupons, carriers,
                       loyalty_tiers, reviews, products, users
              RESTART IDENTITY CASCADE"
        );
        await _pool.DisposeAsync();
    }

    private HttpClient ClientFor(Action<Microsoft.AspNetCore.Routing.IEndpointRouteBuilder> map, string? emailOverride = null)
    {
        var server = OrchestratorTestHost.Create(_pool, map);
        var client = server.CreateClient();
        client.DefaultRequestHeaders.Add("X-Test-Email", emailOverride ?? Email);
        return client;
    }

    // ─────────────────────── cart ────────────────────────────

    [Fact]
    public async Task GetCart_LazyCreatesEmptyCart()
    {
        using var client = ClientFor(r => r.MapCartRoutes());
        var response = await client.GetAsync("/api/cart");
        response.EnsureSuccessStatusCode();
        var payload = await response.Content.ReadFromJsonAsync<JsonElement>();
        payload.GetProperty("item_count").GetInt32().Should().Be(0);
        payload.GetProperty("items").GetArrayLength().Should().Be(0);
    }

    [Fact]
    public async Task AddCartItem_UpsertsAndIncrementsQuantity()
    {
        using var client = ClientFor(r => r.MapCartRoutes());
        var first = await client.PostAsJsonAsync(
            "/api/cart/items",
            new { product_id = _productAId.ToString(), quantity = 2 }
        );
        first.EnsureSuccessStatusCode();

        var second = await client.PostAsJsonAsync(
            "/api/cart/items",
            new { product_id = _productAId.ToString(), quantity = 3 }
        );
        second.EnsureSuccessStatusCode();

        var cart = await client.GetFromJsonAsync<JsonElement>("/api/cart");
        cart.GetProperty("item_count").GetInt32().Should().Be(5);
    }

    [Fact]
    public async Task AddCartItem_404ForMissingProduct()
    {
        using var client = ClientFor(r => r.MapCartRoutes());
        var response = await client.PostAsJsonAsync(
            "/api/cart/items",
            new { product_id = Guid.NewGuid().ToString(), quantity = 1 }
        );
        response.StatusCode.Should().Be(HttpStatusCode.NotFound);
    }

    [Fact]
    public async Task UpdateCartItem_QuantityZeroDeletes()
    {
        using var client = ClientFor(r => r.MapCartRoutes());
        await client.PostAsJsonAsync(
            "/api/cart/items",
            new { product_id = _productAId.ToString(), quantity = 1 }
        );
        var cart = await client.GetFromJsonAsync<JsonElement>("/api/cart");
        var itemId = cart.GetProperty("items")[0].GetProperty("id").GetString()!;

        var update = await client.PutAsJsonAsync(
            $"/api/cart/items/{itemId}",
            new { quantity = 0 }
        );
        update.EnsureSuccessStatusCode();

        var after = await client.GetFromJsonAsync<JsonElement>("/api/cart");
        after.GetProperty("items").GetArrayLength().Should().Be(0);
    }

    [Fact]
    public async Task RemoveCartItem_NotFoundForOtherUsersItem()
    {
        using var client = ClientFor(r => r.MapCartRoutes());
        await client.PostAsJsonAsync(
            "/api/cart/items",
            new { product_id = _productAId.ToString(), quantity = 1 }
        );
        var cart = await client.GetFromJsonAsync<JsonElement>("/api/cart");
        var itemId = cart.GetProperty("items")[0].GetProperty("id").GetString()!;

        // Sign in as another user and try to remove
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            "INSERT INTO users (email, password_hash, name, role) VALUES ('other@example.com', 'x', 'Other', 'customer')"
        );

        using var otherClient = ClientFor(r => r.MapCartRoutes(), emailOverride: "other@example.com");
        var response = await otherClient.DeleteAsync($"/api/cart/items/{itemId}");
        response.StatusCode.Should().Be(HttpStatusCode.NotFound);
    }

    [Fact]
    public async Task ApplyCoupon_AppliesPercentageDiscount()
    {
        using var client = ClientFor(r => r.MapCartRoutes());
        await client.PostAsJsonAsync(
            "/api/cart/items",
            new { product_id = _productAId.ToString(), quantity = 2 }
        );

        var response = await client.PostAsJsonAsync(
            "/api/cart/coupon",
            new { code = "SAVE10" }
        );
        response.EnsureSuccessStatusCode();
        var payload = await response.Content.ReadFromJsonAsync<JsonElement>();
        payload.GetProperty("status").GetString().Should().Be("applied");
        payload.GetProperty("discount_amount").GetDecimal().Should().Be(20m); // 10% of 200
    }

    [Fact]
    public async Task ApplyCoupon_RejectsBelowMinSpend()
    {
        using var client = ClientFor(r => r.MapCartRoutes());
        await client.PostAsJsonAsync(
            "/api/cart/items",
            new { product_id = _productAId.ToString(), quantity = 1 }
        );
        var response = await client.PostAsJsonAsync(
            "/api/cart/coupon",
            new { code = "BIGSPEND" }
        );
        response.StatusCode.Should().Be(HttpStatusCode.BadRequest);
    }

    [Fact]
    public async Task RemoveCoupon_ClearsDiscount()
    {
        using var client = ClientFor(r => r.MapCartRoutes());
        await client.PostAsJsonAsync(
            "/api/cart/items",
            new { product_id = _productAId.ToString(), quantity = 2 }
        );
        await client.PostAsJsonAsync("/api/cart/coupon", new { code = "SAVE10" });
        var delete = await client.DeleteAsync("/api/cart/coupon");
        delete.EnsureSuccessStatusCode();

        var cart = await client.GetFromJsonAsync<JsonElement>("/api/cart");
        cart.GetProperty("discount_amount").GetDecimal().Should().Be(0m);
        cart.TryGetProperty("coupon_code", out var coupon).Should().BeTrue();
        coupon.ValueKind.Should().Be(JsonValueKind.Null);
    }

    [Fact]
    public async Task UpdateCartAddress_SetsBothWhenSame()
    {
        using var client = ClientFor(r => r.MapCartRoutes());
        var addr = new
        {
            shipping_address = new
            {
                street = "1 Main",
                city = "SF",
                state = "CA",
                zip = "94105",
                country = "US",
            },
            billing_same_as_shipping = true,
        };
        var response = await client.PutAsJsonAsync("/api/cart/address", addr);
        response.EnsureSuccessStatusCode();

        var cart = await client.GetFromJsonAsync<JsonElement>("/api/cart");
        cart.GetProperty("shipping_address").GetProperty("street").GetString().Should().Be("1 Main");
        cart.GetProperty("billing_address").GetProperty("street").GetString().Should().Be("1 Main");
        cart.GetProperty("billing_same_as_shipping").GetBoolean().Should().BeTrue();
    }

    // ─────────────────────── checkout ────────────────────────

    [Fact]
    public async Task Checkout_HappyPath_CreatesOrderClearsCart()
    {
        using var cartClient = ClientFor(r => r.MapCartRoutes());
        await cartClient.PostAsJsonAsync(
            "/api/cart/items",
            new { product_id = _productAId.ToString(), quantity = 2 }
        );

        using var client = ClientFor(r => r.MapCheckoutRoutes());
        var body = new
        {
            shipping_address = new
            {
                street = "1 Main",
                city = "SF",
                state = "CA",
                zip = "94105",
                country = "US",
            },
            billing_same_as_shipping = true,
        };
        var response = await client.PostAsJsonAsync("/api/checkout", body);
        response.EnsureSuccessStatusCode();
        var payload = await response.Content.ReadFromJsonAsync<JsonElement>();
        payload.GetProperty("status").GetString().Should().Be("placed");
        payload.GetProperty("item_count").GetInt32().Should().Be(1);
        payload.GetProperty("tracking_number").GetString().Should().StartWith("TRK-");

        // Cart is empty afterwards
        var cart = await cartClient.GetFromJsonAsync<JsonElement>("/api/cart");
        cart.GetProperty("item_count").GetInt32().Should().Be(0);

        // Inventory decremented
        await using var conn = await _pool.OpenAsync();
        var stock = await conn.ExecuteScalarAsync<int>(
            "SELECT quantity FROM warehouse_inventory WHERE warehouse_id = @w AND product_id = @p",
            new { w = _warehouseId, p = _productAId }
        );
        stock.Should().Be(10); // started with 12, deducted 2
    }

    [Fact]
    public async Task Checkout_EmptyCart_ReturnsBadRequest()
    {
        using var client = ClientFor(r => r.MapCheckoutRoutes());
        var body = new
        {
            shipping_address = new
            {
                street = "1 Main",
                city = "SF",
                state = "CA",
                zip = "94105",
                country = "US",
            },
        };
        var response = await client.PostAsJsonAsync("/api/checkout", body);
        response.StatusCode.Should().Be(HttpStatusCode.BadRequest);
    }

    [Fact]
    public async Task Checkout_InsufficientStock_ReturnsBadRequest()
    {
        using var cartClient = ClientFor(r => r.MapCartRoutes());
        await cartClient.PostAsJsonAsync(
            "/api/cart/items",
            new { product_id = _productBId.ToString(), quantity = 99 }
        );

        using var client = ClientFor(r => r.MapCheckoutRoutes());
        var body = new
        {
            shipping_address = new
            {
                street = "1 Main",
                city = "SF",
                state = "CA",
                zip = "94105",
                country = "US",
            },
        };
        var response = await client.PostAsJsonAsync("/api/checkout", body);
        response.StatusCode.Should().Be(HttpStatusCode.BadRequest);
    }

    [Fact]
    public async Task Checkout_AppliesLoyaltyDiscount()
    {
        // Upgrade user to gold (20% off)
        await using (var setup = await _pool.OpenAsync())
        {
            await setup.ExecuteAsync(
                "UPDATE users SET loyalty_tier = 'gold', total_spend = 5000 WHERE id = @u",
                new { u = _userId }
            );
        }

        using var cartClient = ClientFor(r => r.MapCartRoutes());
        await cartClient.PostAsJsonAsync(
            "/api/cart/items",
            new { product_id = _productAId.ToString(), quantity = 1 }
        );

        using var client = ClientFor(r => r.MapCheckoutRoutes());
        var body = new
        {
            shipping_address = new
            {
                street = "1 Main",
                city = "SF",
                state = "CA",
                zip = "94105",
                country = "US",
            },
        };
        var response = await client.PostAsJsonAsync("/api/checkout", body);
        response.EnsureSuccessStatusCode();
        var payload = await response.Content.ReadFromJsonAsync<JsonElement>();
        // Product A price=100, 20% off => 80
        payload.GetProperty("total").GetDecimal().Should().Be(80m);
    }

    // ─────────────────────── profile ─────────────────────────

    [Fact]
    public async Task GetProfile_ReturnsIdentityAndTierBenefits()
    {
        await using (var conn = await _pool.OpenAsync())
        {
            await conn.ExecuteAsync(
                "UPDATE users SET loyalty_tier = 'silver', total_spend = 1250.50 WHERE id = @u",
                new { u = _userId }
            );
        }
        using var client = ClientFor(r => r.MapProfileRoutes());
        var response = await client.GetAsync("/api/profile");
        response.EnsureSuccessStatusCode();
        var payload = await response.Content.ReadFromJsonAsync<JsonElement>();
        payload.GetProperty("email").GetString().Should().Be(Email);
        payload.GetProperty("loyalty_tier").GetString().Should().Be("silver");
        payload.GetProperty("total_spend").GetDecimal().Should().Be(1250.50m);
        payload.GetProperty("tier_benefits").GetProperty("discount_pct").GetDecimal().Should().Be(10m);
    }

    [Fact]
    public async Task GetProfile_404ForMissingUser()
    {
        using var client = ClientFor(r => r.MapProfileRoutes(), emailOverride: "nobody@example.com");
        var response = await client.GetAsync("/api/profile");
        response.StatusCode.Should().Be(HttpStatusCode.NotFound);
    }

    // ─────────────────────── return label ────────────────────

    [Fact]
    public async Task ReturnLabel_ReturnsPdfContent()
    {
        await using (var conn = await _pool.OpenAsync())
        {
            var orderId = await conn.ExecuteScalarAsync<Guid>(
                @"INSERT INTO orders (user_id, status, total, shipping_address, shipping_carrier)
                  VALUES (@u, 'delivered', 25, @a::jsonb, 'Standard Shipping') RETURNING id",
                new
                {
                    u = _userId,
                    a = "{\"street\":\"100 Test\",\"city\":\"SF\",\"state\":\"CA\",\"zip\":\"94105\"}",
                }
            );
            await conn.ExecuteAsync(
                @"INSERT INTO returns (order_id, user_id, reason, status, return_label_url, refund_method, refund_amount)
                  VALUES (@o, @u, 'damaged', 'requested', '/api/returns/abc123xyz/label', 'store_credit', 25)",
                new { o = orderId, u = _userId }
            );
        }

        using var client = ClientFor(r => r.MapReturnLabelRoutes());
        var response = await client.GetAsync("/api/returns/abc123xyz/label");
        response.EnsureSuccessStatusCode();
        response.Content.Headers.ContentType!.MediaType.Should().Be("application/pdf");
        var bytes = await response.Content.ReadAsByteArrayAsync();
        bytes.Length.Should().BeGreaterThan(500);
        System.Text.Encoding.Latin1.GetString(bytes, 0, 8).Should().StartWith("%PDF-1.4");
    }

    [Fact]
    public async Task ReturnLabel_404ForUnknownToken()
    {
        using var client = ClientFor(r => r.MapReturnLabelRoutes());
        var response = await client.GetAsync("/api/returns/doesnotexist/label");
        response.StatusCode.Should().Be(HttpStatusCode.NotFound);
    }

    // ─────────────────────── seed ────────────────────────────

    private async Task SeedAsync()
    {
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            @"TRUNCATE order_status_history, order_items, returns, orders,
                       cart_items, carts, messages, conversations,
                       warehouse_inventory, warehouses, coupons, carriers,
                       loyalty_tiers, reviews, products, users
              RESTART IDENTITY CASCADE"
        );

        _userId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO users (email, password_hash, name, role, total_spend)
              VALUES (@email, 'x', 'Tester', 'customer', 0) RETURNING id",
            new { email = Email }
        );

        _productAId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO products (name, description, category, brand, price, rating, review_count, is_active)
              VALUES ('Widget A', 'Sample', 'Electronics', 'X', 100, 4.5, 1, TRUE) RETURNING id"
        );
        _productBId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO products (name, description, category, brand, price, rating, review_count, is_active)
              VALUES ('Widget B', 'Sample', 'Electronics', 'X', 50, 4.0, 1, TRUE) RETURNING id"
        );
        _warehouseId = await conn.ExecuteScalarAsync<Guid>(
            "INSERT INTO warehouses (name, location, region) VALUES ('East','Richmond,VA','east') RETURNING id"
        );
        await conn.ExecuteAsync(
            @"INSERT INTO warehouse_inventory (warehouse_id, product_id, quantity)
              VALUES (@w, @a, 12), (@w, @b, 3)",
            new { w = _warehouseId, a = _productAId, b = _productBId }
        );

        await conn.ExecuteAsync(
            @"INSERT INTO carriers (name, speed_tier, base_rate)
              VALUES ('Standard Shipping', 'standard', 5.00)"
        );

        await conn.ExecuteAsync(
            @"INSERT INTO loyalty_tiers (name, min_spend, discount_pct, free_shipping_threshold, priority_support)
              VALUES ('bronze', 0, 0, NULL, FALSE),
                     ('silver', 500, 10, 100, FALSE),
                     ('gold', 2000, 20, 50, TRUE)"
        );

        await conn.ExecuteAsync(
            @"INSERT INTO coupons (code, description, discount_type, discount_value, min_spend,
                                   max_discount, usage_limit, times_used, valid_until, is_active)
              VALUES ('SAVE10', '10% off', 'percentage', 10, 0, NULL, NULL, 0, NOW() + INTERVAL '30 days', TRUE),
                     ('BIGSPEND', 'Min $500', 'percentage', 15, 500, NULL, NULL, 0, NOW() + INTERVAL '30 days', TRUE)"
        );
    }
}
