using ECommerceAgents.Orchestrator.Routes;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;
using Microsoft.AspNetCore.TestHost;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using System.Text.Json;

namespace ECommerceAgents.Orchestrator.Tests;

/// <summary>
/// Minimal in-memory host for testing route handlers. Wires the real
/// <see cref="DatabasePool"/> so routes hit the Postgres testcontainer,
/// but replaces auth with a middleware that just stamps
/// <see cref="RequestContext.CurrentUserEmail"/> from the
/// <c>X-Test-Email</c> header — no JWT, no signup required.
/// </summary>
public static class OrchestratorTestHost
{
    public static TestServer Create(DatabasePool pool, Action<IEndpointRouteBuilder> mapRoutes)
    {
        var hostBuilder = new HostBuilder()
            .ConfigureWebHost(web =>
            {
                web.UseTestServer();
                web.ConfigureServices(services =>
                {
                    services.AddSingleton(pool);
                    services.AddSingleton(new AgentSettings { DatabaseUrl = pool.DataSource.ConnectionString });
                    services.AddRouting();
                    services.ConfigureHttpJsonOptions(opts =>
                    {
                        opts.SerializerOptions.PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower;
                        opts.SerializerOptions.DictionaryKeyPolicy = JsonNamingPolicy.SnakeCaseLower;
                        opts.SerializerOptions.PropertyNameCaseInsensitive = true;
                    });
                });
                web.Configure(app =>
                {
                    app.Use(async (ctx, next) =>
                    {
                        var email = ctx.Request.Headers["X-Test-Email"].ToString();
                        var role = ctx.Request.Headers["X-Test-Role"].ToString();
                        if (string.IsNullOrEmpty(role)) role = "customer";
                        using var scope = RequestContext.Scope(email, role, "");
                        await next();
                    });
                    app.UseRouting();
                    app.UseEndpoints(endpoints => mapRoutes(endpoints));
                });
            });

        var host = hostBuilder.Start();
        return host.GetTestServer();
    }
}
