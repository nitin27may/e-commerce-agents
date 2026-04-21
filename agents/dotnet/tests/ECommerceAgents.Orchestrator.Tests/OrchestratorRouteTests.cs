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

[CollectionDefinition(nameof(LocalPostgresCollection))]
public sealed class LocalPostgresCollection : ICollectionFixture<PostgresFixture> { }

[Collection(nameof(LocalPostgresCollection))]
public sealed class OrchestratorRouteTests : IAsyncLifetime
{
    private readonly PostgresFixture _pg;
    private DatabasePool _pool = null!;
    private const string Email = "routes@example.com";
    private Guid _userId;
    private Guid _productId;
    private Guid _orderPlacedId;
    private Guid _orderDeliveredId;
    private Guid _conversationId;

    public OrchestratorRouteTests(PostgresFixture pg) => _pg = pg;

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
                       messages, conversations, warehouse_inventory,
                       warehouses, reviews, products, users
              RESTART IDENTITY CASCADE"
        );
        await _pool.DisposeAsync();
    }

    private HttpClient ClientFor(Action<Microsoft.AspNetCore.Routing.IEndpointRouteBuilder> map)
    {
        var server = OrchestratorTestHost.Create(_pool, map);
        var client = server.CreateClient();
        client.DefaultRequestHeaders.Add("X-Test-Email", Email);
        return client;
    }

    // ─────────────────────── conversations ───────────────────

    [Fact]
    public async Task ListConversations_ReturnsOnlyActiveForCurrentUser()
    {
        using var client = ClientFor(r => r.MapConversationRoutes());
        var response = await client.GetAsync("/api/conversations");
        response.EnsureSuccessStatusCode();
        var payload = await response.Content.ReadFromJsonAsync<JsonElement>();
        payload.GetArrayLength().Should().Be(1);
        payload[0].GetProperty("id").GetString().Should().Be(_conversationId.ToString());
    }

    [Fact]
    public async Task GetConversation_ReturnsMessages()
    {
        using var client = ClientFor(r => r.MapConversationRoutes());
        var response = await client.GetAsync($"/api/conversations/{_conversationId}");
        response.EnsureSuccessStatusCode();
        var payload = await response.Content.ReadFromJsonAsync<JsonElement>();
        payload.GetProperty("messages").GetArrayLength().Should().Be(2);
    }

    [Fact]
    public async Task GetConversation_NotFoundForBadId()
    {
        using var client = ClientFor(r => r.MapConversationRoutes());
        var response = await client.GetAsync($"/api/conversations/{Guid.NewGuid()}");
        response.StatusCode.Should().Be(HttpStatusCode.NotFound);
    }

    [Fact]
    public async Task DeleteConversation_SoftDeletesAndHidesFromList()
    {
        using var client = ClientFor(r => r.MapConversationRoutes());
        var delete = await client.DeleteAsync($"/api/conversations/{_conversationId}");
        delete.EnsureSuccessStatusCode();

        var list = await client.GetFromJsonAsync<JsonElement>("/api/conversations");
        list.GetArrayLength().Should().Be(0);
    }

    // ─────────────────────── products ────────────────────────

    [Fact]
    public async Task ListProducts_ReturnsCatalogue()
    {
        using var client = ClientFor(r => r.MapProductRoutes());
        var response = await client.GetAsync("/api/products?limit=10");
        response.EnsureSuccessStatusCode();
        var payload = await response.Content.ReadFromJsonAsync<JsonElement>();
        payload.GetProperty("total").GetInt32().Should().BeGreaterThan(0);
        payload.GetProperty("products").GetArrayLength().Should().BeGreaterThan(0);
        payload.GetProperty("categories").GetArrayLength().Should().BeGreaterThan(0);
    }

    [Fact]
    public async Task ListProducts_FiltersByCategory()
    {
        using var client = ClientFor(r => r.MapProductRoutes());
        var response = await client.GetAsync("/api/products?category=Nothing");
        response.EnsureSuccessStatusCode();
        var payload = await response.Content.ReadFromJsonAsync<JsonElement>();
        payload.GetProperty("total").GetInt32().Should().Be(0);
    }

    [Fact]
    public async Task GetProduct_HydratesReviewsAndStock()
    {
        using var client = ClientFor(r => r.MapProductRoutes());
        var response = await client.GetAsync($"/api/products/{_productId}");
        response.EnsureSuccessStatusCode();
        var payload = await response.Content.ReadFromJsonAsync<JsonElement>();
        payload.GetProperty("in_stock").GetBoolean().Should().BeTrue();
        payload.GetProperty("warehouses").GetArrayLength().Should().BeGreaterThan(0);
    }

    [Fact]
    public async Task GetProduct_404ForUnknownId()
    {
        using var client = ClientFor(r => r.MapProductRoutes());
        var response = await client.GetAsync($"/api/products/{Guid.NewGuid()}");
        response.StatusCode.Should().Be(HttpStatusCode.NotFound);
    }

    // ─────────────────────── orders ──────────────────────────

    [Fact]
    public async Task ListOrders_ReturnsCallerOrders()
    {
        using var client = ClientFor(r => r.MapOrderRoutes());
        var response = await client.GetAsync("/api/orders");
        response.EnsureSuccessStatusCode();
        var payload = await response.Content.ReadFromJsonAsync<JsonElement>();
        payload.GetProperty("total").GetInt32().Should().Be(2);
    }

    [Fact]
    public async Task GetOrder_404ForOtherUsersOrder()
    {
        using var client = ClientFor(r => r.MapOrderRoutes());
        client.DefaultRequestHeaders.Remove("X-Test-Email");
        client.DefaultRequestHeaders.Add("X-Test-Email", "ghost@example.com");
        var response = await client.GetAsync($"/api/orders/{_orderPlacedId}");
        response.StatusCode.Should().Be(HttpStatusCode.NotFound);
    }

    [Fact]
    public async Task CancelOrder_RejectsDeliveredStatus()
    {
        using var client = ClientFor(r => r.MapOrderRoutes());
        var response = await client.PostAsJsonAsync(
            $"/api/orders/{_orderDeliveredId}/cancel",
            new { reason = "change of mind" }
        );
        response.StatusCode.Should().Be(HttpStatusCode.BadRequest);
    }

    [Fact]
    public async Task CancelOrder_HappyPath_TransitionsStatus()
    {
        using var client = ClientFor(r => r.MapOrderRoutes());
        var response = await client.PostAsJsonAsync(
            $"/api/orders/{_orderPlacedId}/cancel",
            new { reason = "ordered wrong size" }
        );
        response.EnsureSuccessStatusCode();
        var payload = await response.Content.ReadFromJsonAsync<JsonElement>();
        payload.GetProperty("status").GetString().Should().Be("cancelled");
    }

    [Fact]
    public async Task ReturnOrder_RejectsNonDeliveredStatus()
    {
        using var client = ClientFor(r => r.MapOrderRoutes());
        var response = await client.PostAsJsonAsync(
            $"/api/orders/{_orderPlacedId}/return",
            new { reason = "defective" }
        );
        response.StatusCode.Should().Be(HttpStatusCode.BadRequest);
    }

    [Fact]
    public async Task ReturnOrder_HappyPath_CreatesReturn()
    {
        using var client = ClientFor(r => r.MapOrderRoutes());
        var response = await client.PostAsJsonAsync(
            $"/api/orders/{_orderDeliveredId}/return",
            new { reason = "arrived broken", refundMethod = "store_credit" }
        );
        response.EnsureSuccessStatusCode();
        var payload = await response.Content.ReadFromJsonAsync<JsonElement>();
        payload.GetProperty("return_label_url").GetString().Should().StartWith("/api/returns/");
        payload.GetProperty("refund_method").GetString().Should().Be("store_credit");
    }

    [Fact]
    public async Task ReturnOrder_DoubleRequestRejectedOnStatusTransition()
    {
        // Matches Python's behaviour: the first successful return
        // transitions the order to 'returned', so the second call is
        // rejected by the status check (400) before it reaches the
        // existing-return check (409). The 409 path is covered by a
        // separate flow where the return was created out-of-band.
        using var client = ClientFor(r => r.MapOrderRoutes());
        var first = await client.PostAsJsonAsync(
            $"/api/orders/{_orderDeliveredId}/return",
            new { reason = "wrong colour" }
        );
        first.EnsureSuccessStatusCode();
        var second = await client.PostAsJsonAsync(
            $"/api/orders/{_orderDeliveredId}/return",
            new { reason = "still wrong" }
        );
        second.StatusCode.Should().Be(HttpStatusCode.BadRequest);
    }

    // ─────────────────────── seed ────────────────────────────

    private async Task SeedAsync()
    {
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            @"TRUNCATE order_status_history, order_items, returns, orders,
                       messages, conversations, warehouse_inventory,
                       warehouses, reviews, products, users
              RESTART IDENTITY CASCADE"
        );

        _userId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO users (email, password_hash, name, role)
              VALUES (@email, 'x', 'Tester', 'customer') RETURNING id",
            new { email = Email }
        );

        _productId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO products (name, description, category, brand, price, rating, review_count)
              VALUES ('Headphones', 'Sample', 'Electronics', 'X', 200, 4.5, 1)
              RETURNING id"
        );
        var wh = await conn.ExecuteScalarAsync<Guid>(
            "INSERT INTO warehouses (name, location, region) VALUES ('East','Richmond,VA','east') RETURNING id"
        );
        await conn.ExecuteAsync(
            "INSERT INTO warehouse_inventory (warehouse_id, product_id, quantity) VALUES (@wh, @pid, 12)",
            new { wh, pid = _productId }
        );

        const string addr = "{\"street\":\"100 Test\",\"city\":\"SF\",\"state\":\"CA\",\"zip\":\"94105\",\"country\":\"US\"}";
        _orderPlacedId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO orders (user_id, status, total, shipping_address)
              VALUES (@uid, 'placed', 19.99, @addr::jsonb) RETURNING id",
            new { uid = _userId, addr }
        );
        _orderDeliveredId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO orders (user_id, status, total, shipping_address)
              VALUES (@uid, 'delivered', 39.98, @addr::jsonb) RETURNING id",
            new { uid = _userId, addr }
        );
        await conn.ExecuteAsync(
            @"INSERT INTO order_items (order_id, product_id, quantity, unit_price, subtotal)
              VALUES (@p, @prod, 1, 19.99, 19.99),
                     (@d, @prod, 2, 19.99, 39.98)",
            new { p = _orderPlacedId, d = _orderDeliveredId, prod = _productId }
        );

        _conversationId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO conversations (user_id, title, is_active)
              VALUES (@uid, 'Hello', TRUE) RETURNING id",
            new { uid = _userId }
        );
        await conn.ExecuteAsync(
            @"INSERT INTO messages (conversation_id, role, content)
              VALUES (@cid, 'user', 'hi there'),
                     (@cid, 'assistant', 'hi back')",
            new { cid = _conversationId }
        );
    }
}
