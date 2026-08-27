using ECommerceAgents.Shared.Auth;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.ContextProviders;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Middleware;
using ECommerceAgents.Shared.Telemetry;
using Microsoft.Agents.AI;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Microsoft.Extensions.AI;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;
using System.Runtime.CompilerServices;
using System.Text;
using System.Text.Json;

namespace ECommerceAgents.Shared.A2A;

/// <summary>
/// Per-specialist HTTP shell. Exposes the canonical A2A contract:
/// <list type="bullet">
/// <item><c>GET /</c> and <c>GET /health</c></item>
/// <item><c>GET /.well-known/agent-card.json</c></item>
/// <item><c>POST /message:send</c></item>
/// <item><c>POST /message:stream</c> (issue #14 — see <see cref="RunAgentWithHistoryStreamingAsync"/>)</item>
/// </list>
/// This mirrors Python's <c>shared/agent_host.py</c>. The blocking request
/// delegate is supplied by the caller — the host knows nothing about what
/// the agent actually does with the user message. <c>/message:stream</c> is
/// NOT similarly parameterized: every specialist's <c>Program.cs</c> passes
/// the same <see cref="RunAgentWithHistoryAsync"/> delegate for
/// <c>onMessage</c> today, so its streaming twin is wired directly rather
/// than threading a second delegate through <see cref="Build"/> and all 5
/// call sites for a distinction that doesn't currently exist in practice.
/// </summary>
public static class AgentHost
{
    public sealed record MessagePayload(string Message, List<HistoryEntry>? History);

    /// <summary>
    /// <c>Steps</c> is the .NET twin of Python's A2A response carrying its
    /// own captured timeline steps back to the orchestrator (issue #16) —
    /// serializes as <c>"steps"</c> under ASP.NET Core's Minimal API default
    /// camelCase policy, which <see cref="ECommerceAgents.Shared.A2A.A2AClient"/>'s
    /// own response DTO matches explicitly via <c>JsonPropertyName</c>.
    /// </summary>
    public sealed record AgentResponse(string Response, IReadOnlyList<ExecutionStep>? Steps = null);

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
        AgentSettingsValidator.Validate(
            settings,
            LoggerFactory
                .Create(lb => lb.AddConsole())
                .CreateLogger<AgentHostMarker>()
        );
        builder.Services.AddSingleton(settings);
        builder.Services.AddSingleton(new DatabasePool(settings));
        builder.Services.AddSingleton(new JwtTokenService(settings));
        builder.Services.AddHttpClient<JwksKeyProvider>();
        builder.Services.AddAgentTelemetry(settings);
        builder.Services.AddSingleton<HitlGate>();

        // Cross-cutting agent pipeline (issue #12) — resolved by
        // Agents.SpecialistPipeline / SpecialistAgentFactory.Create when
        // callers pass their IServiceProvider.
        builder.Services.AddSingleton<AgentRunLogger>();
        builder.Services.AddSingleton<ToolAuditMiddleware>();
        builder.Services.AddSingleton<PiiRedactor>();
        builder.Services.AddSingleton<ContextEnricher>();

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
                capabilities = new[] { "message:send", "message:stream" },
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
                return Results.Ok(new AgentResponse(reply, RequestContext.CurrentSteps.ToList()));
            }
            catch (Exception ex)
            {
                logger.LogException(ex, "agent.handler_failure service={Service}", name);
                span?.SetTag("error", true);
                span?.SetTag("error.type", ex.GetType().Name);
                return Results.Problem(detail: ex.Message, statusCode: 500);
            }
        });

        app.MapPost("/message:stream", async (
            [FromBody] MessagePayload payload,
            HttpContext http,
            ILogger<AgentHostMarker> logger,
            IServiceProvider services
        ) =>
        {
            if (string.IsNullOrWhiteSpace(payload?.Message))
            {
                http.Response.StatusCode = 400;
                await http.Response.WriteAsync("message is required");
                return;
            }

            using var span = TelemetrySetup.AgentRunSpan(name, settings.LlmModel);
            var history = payload.History ?? new List<HistoryEntry>();
            using var scope = RequestContext.Scope(
                RequestContext.CurrentUserEmail,
                RequestContext.CurrentUserRole,
                RequestContext.CurrentSessionId,
                history
            );

            http.Response.Headers.ContentType = "text/event-stream";
            http.Response.Headers.CacheControl = "no-cache";
            http.Response.Headers["X-Accel-Buffering"] = "no";

            try
            {
                var stepsSent = 0;

                // Frames for every step recorded since the last drain.
                //
                // Issue #16 sent the specialist's steps as ONE bulk frame after the
                // last chunk. That is the latest possible moment: by then the answer
                // has finished writing and the timeline it describes has stopped
                // being interesting. In a MAF tool loop the tools resolve first and
                // the prose narrating them comes second, so draining before each
                // chunk lets a step overtake the sentence about it.
                //
                // Still "event: steps" (plural) carrying a list, so A2AClient's
                // MergeReturnedSteps — which appends — needs no change; it just
                // receives several small batches instead of one large one.
                async Task DrainStepsAsync()
                {
                    var steps = RequestContext.CurrentSteps;
                    if (steps.Count <= stepsSent)
                    {
                        return;
                    }

                    var fresh = steps.Skip(stepsSent).ToList();
                    stepsSent = steps.Count;
                    await http.Response.WriteAsync(
                        $"event: steps\ndata: {JsonSerializer.Serialize(fresh)}\n\n", Encoding.UTF8);
                    await http.Response.Body.FlushAsync();
                }

                await foreach (var chunk in RunAgentWithHistoryStreamingAsync(services, payload.Message, http.RequestAborted))
                {
                    await DrainStepsAsync();

                    // Same per-line "data:" framing as ChatRoutes.StreamAsync, for the
                    // same reason: a chunk that is itself a lone "\n" must not be sent
                    // as a raw embedded newline, or it prematurely closes the SSE event.
                    var frame = new StringBuilder();
                    foreach (var line in chunk.Split('\n'))
                    {
                        frame.Append("data: ").Append(line).Append('\n');
                    }
                    frame.Append('\n');
                    await http.Response.WriteAsync(frame.ToString(), Encoding.UTF8);
                    await http.Response.Body.FlushAsync();
                }

                // Anything that completed after the final chunk — a tool the model
                // never narrated, or a run that produced no text at all.
                await DrainStepsAsync();
                await http.Response.WriteAsync("data: [DONE]\n\n", Encoding.UTF8);
                await http.Response.Body.FlushAsync();
            }
            catch (Exception ex)
            {
                logger.LogException(ex, "agent.stream_handler_failure service={Service}", name);
                span?.SetTag("error", true);
                span?.SetTag("error.type", ex.GetType().Name);
                // Headers are already sent by this point (text/event-stream was set
                // before the first await) — a status-code change isn't possible; the
                // client instead sees the stream end without a [DONE] marker.
            }
        });

        return app;
    }

    /// <summary>
    /// Shared <c>onMessage</c> implementation for every specialist: prefer the
    /// history forwarded on the A2A payload (already stamped into
    /// <see cref="RequestContext.CurrentHistory"/> by the <c>/message:send</c>
    /// handler above); if the caller didn't forward one, rehydrate it straight
    /// from Postgres via the session id header. Mirrors Python's
    /// <c>body.get("history", None)</c> → <c>_rehydrate_history_from_session</c>
    /// fallback (audit fix #14) exactly.
    /// </summary>
    public static async Task<string> RunAgentWithHistoryAsync(IServiceProvider services, string message)
    {
        var agent = services.GetRequiredService<AIAgent>();
        var pool = services.GetRequiredService<DatabasePool>();

        var history = RequestContext.CurrentHistory.Count > 0
            ? RequestContext.CurrentHistory.ToList()
            : await HistoryRehydrator.RehydrateAsync(pool, RequestContext.CurrentSessionId) ?? new List<HistoryEntry>();

        var messages = history
            .Where(h => h.Role is "user" or "assistant")
            .Select(h => new ChatMessage(h.Role == "assistant" ? ChatRole.Assistant : ChatRole.User, h.Content))
            .ToList();
        messages.Add(new ChatMessage(ChatRole.User, message));

        var response = await agent.RunAsync(messages);
        return response.Text;
    }

    /// <summary>
    /// Streaming twin of <see cref="RunAgentWithHistoryAsync"/>, backing
    /// <c>POST /message:stream</c> (issue #14). Yields non-empty text deltas
    /// only — tool-call-only updates (<c>AgentResponseUpdate.Text</c> empty,
    /// content carried in <c>Contents</c> instead) are skipped, matching
    /// <c>ChatRoutes.StreamAsync</c>'s own filter; a specialist has no step
    /// recorder to surface those through yet (tracked separately as #16).
    /// </summary>
    public static async IAsyncEnumerable<string> RunAgentWithHistoryStreamingAsync(
        IServiceProvider services,
        string message,
        [EnumeratorCancellation] CancellationToken ct = default
    )
    {
        var agent = services.GetRequiredService<AIAgent>();
        var pool = services.GetRequiredService<DatabasePool>();

        var history = RequestContext.CurrentHistory.Count > 0
            ? RequestContext.CurrentHistory.ToList()
            : await HistoryRehydrator.RehydrateAsync(pool, RequestContext.CurrentSessionId) ?? new List<HistoryEntry>();

        var messages = history
            .Where(h => h.Role is "user" or "assistant")
            .Select(h => new ChatMessage(h.Role == "assistant" ? ChatRole.Assistant : ChatRole.User, h.Content))
            .ToList();
        messages.Add(new ChatMessage(ChatRole.User, message));

        await foreach (var update in agent.RunStreamingAsync(messages, cancellationToken: ct))
        {
            if (!string.IsNullOrEmpty(update.Text))
            {
                yield return update.Text;
            }
        }
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
