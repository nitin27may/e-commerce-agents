using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.TestHost;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using ModelContextProtocol.Client;
using ModelContextProtocol.Protocol;
using Xunit;

namespace ECommerceAgents.Mcp.Tests;

/// <summary>
/// End-to-end coverage through a real <see cref="McpClient"/> talking the
/// actual MCP wire protocol (JSON-RPC over streamable HTTP) to an in-memory
/// <see cref="TestServer"/> — proof this server is a real MCP endpoint a
/// real client can discover tools from and call, not just a REST surface
/// shaped like one (see issue #13). <see cref="McpToolTests"/> covers the
/// SQL/shape logic directly; <see cref="McpAuthTests"/> covers the bearer-
/// token gate. This file is the seam between them: dispatch through the
/// protocol layer itself.
/// </summary>
[Collection(nameof(LocalPostgresCollection))]
public sealed class McpProtocolTests : IAsyncLifetime
{
    private readonly PostgresFixture _pg;
    private DatabasePool _pool = null!;
    private TestServer _server = null!;
    private Guid _productId;

    public McpProtocolTests(PostgresFixture pg) => _pg = pg;

    public async Task InitializeAsync()
    {
        var settings = new AgentSettings { DatabaseUrl = _pg.ConnectionString };
        _pool = new DatabasePool(settings);

        await using (var conn = await _pool.OpenAsync())
        {
            await conn.ExecuteAsync_Truncate();
        }

        _productId = await SeedAsync();

        var hostBuilder = new HostBuilder().ConfigureWebHost(web =>
        {
            web.UseTestServer();
            web.ConfigureServices(services =>
            {
                services.AddSingleton(_pool);
                services.AddSingleton(settings);
                services.AddRouting();
                services.AddMcpServer()
                    .WithHttpTransport(o => o.Stateless = true)
                    .WithToolsFromAssembly(typeof(McpTools).Assembly);
            });
            web.Configure(app =>
            {
                app.UseRouting();
                app.UseMcpAuthGate();
                app.UseEndpoints(endpoints =>
                {
                    endpoints.MapMcpHealthEndpoints();
                    endpoints.MapMcp(McpEndpoints.McpRoutePrefix);
                });
            });
        });

        _server = hostBuilder.Start().GetTestServer();
    }

    public async Task DisposeAsync()
    {
        _server.Dispose();
        await _pool.DisposeAsync();
    }

    private async Task<McpClient> ConnectAsync()
    {
        var httpClient = _server.CreateClient();
        var transport = new HttpClientTransport(
            new HttpClientTransportOptions { Endpoint = new Uri(httpClient.BaseAddress!, McpEndpoints.McpRoutePrefix) },
            httpClient
        );
        return await McpClient.CreateAsync(transport, cancellationToken: CancellationToken.None);
    }

    [Fact]
    public async Task ListTools_ExposesAllThreeInventoryTools()
    {
        await using var client = await ConnectAsync();

        var tools = await client.ListToolsAsync(cancellationToken: CancellationToken.None);

        tools.Select(t => t.Name).Should().BeEquivalentTo(
            new[] { "check_stock", "get_warehouses", "estimate_shipping" }
        );
    }

    [Fact]
    public async Task CallTool_GetWarehouses_ReturnsSeededWarehouses()
    {
        await using var client = await ConnectAsync();

        var result = await client.CallToolAsync(
            "get_warehouses",
            arguments: null,
            cancellationToken: CancellationToken.None
        );

        result.IsError.Should().NotBeTrue();
        var text = result.Content.OfType<TextContentBlock>().FirstOrDefault()?.Text;
        text.Should().NotBeNullOrEmpty();
        text.Should().Contain("East").And.Contain("east"); // seeded warehouse name + region
    }

    [Fact]
    public async Task CallTool_CheckStock_ReturnsRealInventoryThroughTheProtocol()
    {
        await using var client = await ConnectAsync();

        var result = await client.CallToolAsync(
            "check_stock",
            new Dictionary<string, object?> { ["productId"] = _productId.ToString() },
            cancellationToken: CancellationToken.None
        );

        result.IsError.Should().NotBeTrue();
        var text = result.Content.OfType<TextContentBlock>().FirstOrDefault()?.Text;
        text.Should().NotBeNullOrEmpty();
        text.Should().Contain("true"); // inStock
    }

    private async Task<Guid> SeedAsync()
    {
        await using var conn = await _pool.OpenAsync();
        var productId = await Dapper.SqlMapper.ExecuteScalarAsync<Guid>(
            conn,
            @"INSERT INTO products (name, description, category, brand, price)
              VALUES ('Protocol Test Headphones', 'Sample', 'Electronics', 'X', 150)
              RETURNING id"
        );
        var warehouseEast = await Dapper.SqlMapper.ExecuteScalarAsync<Guid>(
            conn,
            "INSERT INTO warehouses (name, location, region) VALUES ('East', 'Richmond, VA', 'east') RETURNING id"
        );
        await Dapper.SqlMapper.ExecuteAsync(
            conn,
            @"INSERT INTO warehouse_inventory (warehouse_id, product_id, quantity, reorder_threshold)
              VALUES (@east, @pid, 8, 5)",
            new { east = warehouseEast, pid = productId }
        );
        return productId;
    }
}

internal static class TestCleanupExtensions
{
    public static Task ExecuteAsync_Truncate(this Npgsql.NpgsqlConnection conn) =>
        Dapper.SqlMapper.ExecuteAsync(
            conn,
            @"TRUNCATE shipping_rates, carriers, warehouse_inventory,
                       warehouses, products RESTART IDENTITY CASCADE"
        );
}
