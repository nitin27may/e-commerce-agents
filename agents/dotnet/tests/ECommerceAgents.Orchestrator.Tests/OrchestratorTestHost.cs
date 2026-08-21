using ECommerceAgents.Orchestrator.Routes;
using ECommerceAgents.Shared.Auth;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.RateLimiting;
using ECommerceAgents.Shared.Orchestration;
using ECommerceAgents.Orchestrator.Modes;
using Microsoft.Agents.AI;
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
    public static TestServer Create(
        DatabasePool pool,
        Action<IEndpointRouteBuilder> mapRoutes,
        AgentSettings? settingsOverride = null,
        HttpMessageHandler? authServerHandler = null,
        Action<IServiceCollection>? configureServices = null
    )
    {
        var settings = (settingsOverride ?? new AgentSettings()) with
        {
            DatabaseUrl = pool.DataSource.ConnectionString,
        };

        var hostBuilder = new HostBuilder()
            .ConfigureWebHost(web =>
            {
                web.UseTestServer();
                web.ConfigureServices(services =>
                {
                    services.AddSingleton(pool);
                    services.AddSingleton(settings);
                    services.AddSingleton(new JwtTokenService(settings));
                    // Chat routes carry a rate-limit endpoint filter (#30), so the
                    // test host has to supply the same dependency the app does.
                    // Redis is unreachable in most test runs and the limiter fails
                    // open, so this stays a no-op unless a test opts in.
                    services.AddSingleton<SlidingWindowRateLimiter>();
                    // ChatRoutes resolves the mode registry now (#33 PR 5).
                    // Registered with the tool router only: a test host has no
                    // database pool wired for the workflow modes, and every
                    // existing chat test exercises the default path anyway.
                    services.AddSingleton<IOrchestrationMode>(sp => new ToolRouterMode(sp.GetRequiredService<AIAgent>()));
                    services.AddSingleton<ModeRegistry>();
                    services.AddLogging();
                    services.AddSingleton(new AuthServerClient(
                        new HttpClient(authServerHandler ?? new HttpClientHandler()),
                        settings
                    ));
                    services.AddRouting();
                    services.ConfigureHttpJsonOptions(opts =>
                    {
                        opts.SerializerOptions.PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower;
                        opts.SerializerOptions.DictionaryKeyPolicy = JsonNamingPolicy.SnakeCaseLower;
                        opts.SerializerOptions.PropertyNameCaseInsensitive = true;
                    });
                    configureServices?.Invoke(services);
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
