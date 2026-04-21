using ECommerceAgents.Shared.Auth;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Telemetry;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;
using System.Text.Json;

namespace ECommerceAgents.Shared.A2A;

/// <summary>
/// Per-specialist HTTP shell. Exposes the canonical A2A contract:
/// <list type="bullet">
/// <item><c>GET /</c> and <c>GET /health</c></item>
/// <item><c>GET /.well-known/agent-card.json</c></item>
/// <item><c>POST /message:send</c></item>
/// </list>
/// This mirrors Python's <c>shared/agent_host.py</c>. The request delegate
/// is supplied by the caller — the host knows nothing about what the
/// agent actually does with the user message.
/// </summary>
public static class AgentHost
{
    public sealed record MessagePayload(string Message, List<HistoryEntry>? History);

    public sealed record AgentResponse(string Response);

    /// <summary>
    /// Build a standalone <see cref="WebApplication"/> configured as an A2A
    /// specialist endpoint.
    /// </summary>
    /// <param name="name">Agent name (used in the agent-card + spans).</param>
    /// <param name="description">Human-readable description for the agent-card.</param>
    /// <param name="port">HTTP port to bind.</param>
    /// <param name="onMessage">Delegate invoked for each <c>/message:send</c> request.</param>
    /// <param name="configureServices">Optional extra DI wiring (tools, agent factory, DB).</param>
    public static WebApplication Build(
        string name,
        string description,
        int port,
        Func<string, IServiceProvider, Task<string>> onMessage,
        Action<WebApplicationBuilder, AgentSettings>? configureServices = null
    )
    {
        var builder = WebApplication.CreateBuilder();

        var settings = AgentSettingsLoader.Load(builder.Configuration);
        builder.Services.AddSingleton(settings);
        builder.Services.AddSingleton(new DatabasePool(settings));
        builder.Services.AddSingleton(new JwtTokenService(settings));
        builder.Services.AddAgentTelemetry(settings);

        configureServices?.Invoke(builder, settings);

        var app = builder.Build();

        app.UseAgentAuth();

        app.MapGet("/", () => Results.Ok(new { status = "ok", service = name, port }));
        app.MapGet("/health", () => Results.Ok(new { healthy = true, service = name }));

        app.MapGet("/.well-known/agent-card.json", () =>
            Results.Ok(new
            {
                name,
                description,
                url = $"http://0.0.0.0:{port}",
                capabilities = new[] { "message:send" },
                transport = "a2a",
            })
        );

        app.MapPost("/message:send", async (
            [FromBody] MessagePayload payload,
            HttpContext http,
            ILogger<AgentHostMarker> logger,
            IServiceProvider services
        ) =>
        {
            if (string.IsNullOrWhiteSpace(payload?.Message))
            {
                return Results.BadRequest(new { detail = "message is required" });
            }

            using var span = TelemetrySetup.AgentRunSpan(name, settings.LlmModel);
            var history = payload.History ?? new List<HistoryEntry>();
            using var scope = RequestContext.Scope(
                RequestContext.CurrentUserEmail,
                RequestContext.CurrentUserRole,
                RequestContext.CurrentSessionId,
                history
            );

            try
            {
                var reply = await onMessage(payload.Message, services);
                return Results.Ok(new AgentResponse(reply));
            }
            catch (Exception ex)
            {
                logger.LogException(ex, "agent.handler_failure service={Service}", name);
                span?.SetTag("error", true);
                span?.SetTag("error.type", ex.GetType().Name);
                return Results.Problem(detail: ex.Message, statusCode: 500);
            }
        });

        return app;
    }
}

internal sealed class AgentHostMarker { }

internal static class LoggingExtensions
{
    public static void LogException(this ILogger logger, Exception ex, string template, params object?[] args)
    {
        var rendered = string.Format(System.Globalization.CultureInfo.InvariantCulture, template.Replace("{Service}", "{0}"), args);
        logger.LogError(ex, "{Message}", rendered);
    }
}
