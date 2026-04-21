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
public sealed class MarketplaceAdminSellerTests : IAsyncLifetime
{
    private readonly PostgresFixture _pg;
    private DatabasePool _pool = null!;
    private const string CustomerEmail = "buyer@example.com";
    private const string SellerEmail = "seller@example.com";
    private const string AdminEmail = "admin@example.com";
    private Guid _buyerId;
    private Guid _sellerId;
    private Guid _adminId;
    private Guid _productId;

    public MarketplaceAdminSellerTests(PostgresFixture pg) => _pg = pg;

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
                       warehouse_inventory, warehouses, agent_execution_steps,
                       usage_logs, agent_permissions, access_requests, agent_catalog,
                       coupons, carriers, loyalty_tiers, reviews, products, users
              RESTART IDENTITY CASCADE"
        );
        await _pool.DisposeAsync();
    }

    private HttpClient ClientFor(
        Action<Microsoft.AspNetCore.Routing.IEndpointRouteBuilder> map,
        string email,
        string role = "customer"
    )
    {
        var server = OrchestratorTestHost.Create(_pool, map);
        var client = server.CreateClient();
        client.DefaultRequestHeaders.Add("X-Test-Email", email);
        client.DefaultRequestHeaders.Add("X-Test-Role", role);
        return client;
    }

    // ─────────────────────── marketplace ─────────────────────

    [Fact]
    public async Task ListAgents_ReturnsActiveCatalog()
    {
        using var client = ClientFor(r => r.MapMarketplaceRoutes(), CustomerEmail);
        var response = await client.GetAsync("/api/marketplace/agents");
        response.EnsureSuccessStatusCode();
        var payload = await response.Content.ReadFromJsonAsync<JsonElement>();
        payload.GetArrayLength().Should().Be(2);
    }

    [Fact]
    public async Task SubmitRequest_AutoApprovesWhenNotRequired()
    {
        using var client = ClientFor(r => r.MapMarketplaceRoutes(), CustomerEmail);
        var response = await client.PostAsJsonAsync(
            "/api/marketplace/request",
            new
            {
                agent_name = "open-access",
                role_requested = "viewer",
                use_case = "Trying it out",
            }
        );
        response.EnsureSuccessStatusCode();
        var payload = await response.Content.ReadFromJsonAsync<JsonElement>();
        payload.GetProperty("status").GetString().Should().Be("approved");

        var my = await client.GetFromJsonAsync<JsonElement>("/api/marketplace/my-agents");
        my.GetArrayLength().Should().Be(1);
        my[0].GetProperty("agent_name").GetString().Should().Be("open-access");
    }

    [Fact]
    public async Task SubmitRequest_CreatesPendingWhenRequired()
    {
        using var client = ClientFor(r => r.MapMarketplaceRoutes(), CustomerEmail);
        var response = await client.PostAsJsonAsync(
            "/api/marketplace/request",
            new
            {
                agent_name = "gated-agent",
                role_requested = "user",
                use_case = "I want to try this",
            }
        );
        response.EnsureSuccessStatusCode();
        var payload = await response.Content.ReadFromJsonAsync<JsonElement>();
        payload.GetProperty("status").GetString().Should().Be("pending");
    }

    [Fact]
    public async Task SubmitRequest_ConflictsOnDuplicatePending()
    {
        using var client = ClientFor(r => r.MapMarketplaceRoutes(), CustomerEmail);
        var first = await client.PostAsJsonAsync(
            "/api/marketplace/request",
            new { agent_name = "gated-agent", role_requested = "user", use_case = "x" }
        );
        first.EnsureSuccessStatusCode();
        var second = await client.PostAsJsonAsync(
            "/api/marketplace/request",
            new { agent_name = "gated-agent", role_requested = "user", use_case = "y" }
        );
        second.StatusCode.Should().Be(HttpStatusCode.Conflict);
    }

    [Fact]
    public async Task SubmitRequest_404ForUnknownAgent()
    {
        using var client = ClientFor(r => r.MapMarketplaceRoutes(), CustomerEmail);
        var response = await client.PostAsJsonAsync(
            "/api/marketplace/request",
            new { agent_name = "nope", role_requested = "user", use_case = "x" }
        );
        response.StatusCode.Should().Be(HttpStatusCode.NotFound);
    }

    // ─────────────────────── admin ───────────────────────────

    [Fact]
    public async Task AdminListRequests_RejectsNonAdmin()
    {
        using var client = ClientFor(r => r.MapAdminRoutes(), CustomerEmail);
        var response = await client.GetAsync("/api/admin/requests");
        response.StatusCode.Should().Be(HttpStatusCode.Forbidden);
    }

    [Fact]
    public async Task AdminApproveRequest_GrantsPermission()
    {
        // Create a pending request as customer
        using var cust = ClientFor(r => r.MapMarketplaceRoutes(), CustomerEmail);
        var create = await cust.PostAsJsonAsync(
            "/api/marketplace/request",
            new { agent_name = "gated-agent", role_requested = "user", use_case = "x" }
        );
        var createdId = (await create.Content.ReadFromJsonAsync<JsonElement>())
            .GetProperty("id").GetString()!;

        using var adminClient = ClientFor(r => r.MapAdminRoutes(), AdminEmail, role: "admin");
        var list = await adminClient.GetFromJsonAsync<JsonElement>("/api/admin/requests");
        list.GetArrayLength().Should().Be(1);

        var approve = await adminClient.PostAsJsonAsync(
            $"/api/admin/requests/{createdId}/approve",
            new { admin_notes = "looks good" }
        );
        approve.EnsureSuccessStatusCode();

        // Verify permission exists
        await using var conn = await _pool.OpenAsync();
        var perm = await conn.ExecuteScalarAsync<Guid?>(
            "SELECT id FROM agent_permissions WHERE user_id = @u AND agent_name = 'gated-agent'",
            new { u = _buyerId }
        );
        perm.Should().NotBeNull();
    }

    [Fact]
    public async Task AdminDenyRequest_SetsDenied()
    {
        using var cust = ClientFor(r => r.MapMarketplaceRoutes(), CustomerEmail);
        var create = await cust.PostAsJsonAsync(
            "/api/marketplace/request",
            new { agent_name = "gated-agent", role_requested = "user", use_case = "x" }
        );
        var id = (await create.Content.ReadFromJsonAsync<JsonElement>())
            .GetProperty("id").GetString()!;

        using var adminClient = ClientFor(r => r.MapAdminRoutes(), AdminEmail, role: "admin");
        var deny = await adminClient.PostAsJsonAsync(
            $"/api/admin/requests/{id}/deny",
            new { admin_notes = "not yet" }
        );
        deny.EnsureSuccessStatusCode();

        // Second deny should 409
        var again = await adminClient.PostAsJsonAsync(
            $"/api/admin/requests/{id}/deny",
            new { admin_notes = "still no" }
        );
        again.StatusCode.Should().Be(HttpStatusCode.Conflict);
    }

    [Fact]
    public async Task AdminUsage_ReturnsZeroWhenNoLogs()
    {
        using var adminClient = ClientFor(r => r.MapAdminRoutes(), AdminEmail, role: "admin");
        var response = await adminClient.GetAsync("/api/admin/usage");
        response.EnsureSuccessStatusCode();
        var payload = await response.Content.ReadFromJsonAsync<JsonElement>();
        payload.GetProperty("period").GetString().Should().Be("last_30_days");
        payload.GetProperty("overall").GetProperty("total_requests").GetInt64().Should().Be(0);
    }

    [Fact]
    public async Task AdminAudit_RespectsLimit()
    {
        await using (var conn = await _pool.OpenAsync())
        {
            for (int i = 0; i < 5; i++)
            {
                await conn.ExecuteAsync(
                    @"INSERT INTO usage_logs (user_id, agent_name, input_summary, tokens_in, tokens_out, tool_calls_count, duration_ms, status)
                      VALUES (@u, 'orchestrator', @s, 10, 20, 1, 100, 'success')",
                    new { u = _buyerId, s = $"req {i}" }
                );
            }
        }

        using var adminClient = ClientFor(r => r.MapAdminRoutes(), AdminEmail, role: "admin");
        var response = await adminClient.GetAsync("/api/admin/audit?limit=3");
        response.EnsureSuccessStatusCode();
        var payload = await response.Content.ReadFromJsonAsync<JsonElement>();
        payload.GetProperty("entries").GetArrayLength().Should().Be(3);
        payload.GetProperty("total").GetInt64().Should().Be(5);
        payload.GetProperty("limit").GetInt32().Should().Be(3);
    }

    // ─────────────────────── seller ──────────────────────────

    [Fact]
    public async Task SellerProducts_RejectsNonSeller()
    {
        using var client = ClientFor(r => r.MapSellerRoutes(), CustomerEmail);
        var response = await client.GetAsync("/api/seller/products");
        response.StatusCode.Should().Be(HttpStatusCode.Forbidden);
    }

    [Fact]
    public async Task SellerProducts_ReturnsOnlyOwnProducts()
    {
        using var client = ClientFor(r => r.MapSellerRoutes(), SellerEmail, role: "seller");
        var response = await client.GetAsync("/api/seller/products");
        response.EnsureSuccessStatusCode();
        var payload = await response.Content.ReadFromJsonAsync<JsonElement>();
        payload.GetProperty("total").GetInt64().Should().Be(1);
        payload.GetProperty("products")[0].GetProperty("id").GetString().Should().Be(_productId.ToString());
    }

    [Fact]
    public async Task SellerOrders_ListsOrdersWithOwnedProduct()
    {
        // Place an order containing the seller's product
        await using (var conn = await _pool.OpenAsync())
        {
            var oid = await conn.ExecuteScalarAsync<Guid>(
                @"INSERT INTO orders (user_id, status, total, shipping_address)
                  VALUES (@u, 'placed', 50, '{""street"":""1 Main"",""city"":""SF""}'::jsonb)
                  RETURNING id",
                new { u = _buyerId }
            );
            await conn.ExecuteAsync(
                @"INSERT INTO order_items (order_id, product_id, quantity, unit_price, subtotal)
                  VALUES (@o, @p, 1, 50, 50)",
                new { o = oid, p = _productId }
            );
        }

        using var client = ClientFor(r => r.MapSellerRoutes(), SellerEmail, role: "seller");
        var response = await client.GetAsync("/api/seller/orders");
        response.EnsureSuccessStatusCode();
        var payload = await response.Content.ReadFromJsonAsync<JsonElement>();
        payload.GetProperty("total").GetInt64().Should().Be(1);
    }

    [Fact]
    public async Task SellerStats_AggregatesRevenue()
    {
        await using (var conn = await _pool.OpenAsync())
        {
            var oid = await conn.ExecuteScalarAsync<Guid>(
                @"INSERT INTO orders (user_id, status, total, shipping_address)
                  VALUES (@u, 'delivered', 150, '{""street"":""1 Main"",""city"":""SF""}'::jsonb)
                  RETURNING id",
                new { u = _buyerId }
            );
            await conn.ExecuteAsync(
                @"INSERT INTO order_items (order_id, product_id, quantity, unit_price, subtotal)
                  VALUES (@o, @p, 3, 50, 150)",
                new { o = oid, p = _productId }
            );
        }

        using var client = ClientFor(r => r.MapSellerRoutes(), SellerEmail, role: "seller");
        var response = await client.GetAsync("/api/seller/stats");
        response.EnsureSuccessStatusCode();
        var payload = await response.Content.ReadFromJsonAsync<JsonElement>();
        payload.GetProperty("product_count").GetInt64().Should().Be(1);
        payload.GetProperty("total_revenue").GetDecimal().Should().Be(150m);
        payload.GetProperty("order_count").GetInt64().Should().Be(1);
    }

    // ─────────────────────── seed ────────────────────────────

    private async Task SeedAsync()
    {
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            @"TRUNCATE order_status_history, order_items, returns, orders,
                       cart_items, carts, messages, conversations,
                       warehouse_inventory, warehouses, agent_execution_steps,
                       usage_logs, agent_permissions, access_requests, agent_catalog,
                       coupons, carriers, loyalty_tiers, reviews, products, users
              RESTART IDENTITY CASCADE"
        );

        _buyerId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO users (email, password_hash, name, role)
              VALUES (@e, 'x', 'Buyer', 'customer') RETURNING id",
            new { e = CustomerEmail }
        );
        _sellerId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO users (email, password_hash, name, role)
              VALUES (@e, 'x', 'Seller', 'seller') RETURNING id",
            new { e = SellerEmail }
        );
        _adminId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO users (email, password_hash, name, role)
              VALUES (@e, 'x', 'Admin', 'admin') RETURNING id",
            new { e = AdminEmail }
        );

        _productId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO products (seller_id, name, description, category, brand, price, rating, review_count, is_active)
              VALUES (@s, 'Widget', 'A thing', 'Electronics', 'X', 50, 4.0, 10, TRUE) RETURNING id",
            new { s = _sellerId }
        );

        await conn.ExecuteAsync(
            @"INSERT INTO agent_catalog (name, display_name, description, requires_approval, allowed_roles)
              VALUES ('gated-agent', 'Gated Agent', 'Needs approval', TRUE, ARRAY['power_user','admin']),
                     ('open-access', 'Open Access', 'Auto approve', FALSE, ARRAY['customer'])"
        );
    }
}
