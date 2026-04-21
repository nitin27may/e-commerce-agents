using ECommerceAgents.Shared.Context;
using Microsoft.Agents.AI;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using System.Text;

namespace ECommerceAgents.Orchestrator.Routes;

/// <summary>
/// Chat endpoints: a blocking <c>POST /api/chat</c> and an SSE
/// <c>POST /api/chat/stream</c>. Both delegate to the orchestrator
/// agent, which picks a specialist via the <c>call_specialist_agent</c>
/// tool.
/// </summary>
public static class ChatRoutes
{
    public sealed record ChatRequest(string Message, List<HistoryEntry>? History);
    public sealed record ChatResponse(string Response, string Agent);

    public static IEndpointRouteBuilder MapChatRoutes(this IEndpointRouteBuilder routes)
    {
        routes.MapPost("/api/chat", SendAsync);
        routes.MapPost("/api/chat/stream", StreamAsync);
        return routes;
    }

    private static async Task<IResult> SendAsync([FromBody] ChatRequest request, AIAgent agent)
    {
        if (string.IsNullOrWhiteSpace(request?.Message))
        {
            return Results.BadRequest(new { detail = "message is required" });
        }

        using var scope = RequestContext.Scope(
            RequestContext.CurrentUserEmail,
            RequestContext.CurrentUserRole,
            RequestContext.CurrentSessionId,
            request.History ?? new List<HistoryEntry>()
        );

        var response = await agent.RunAsync(request.Message);
        return Results.Ok(new ChatResponse(response.Text, "orchestrator"));
    }

    private static async Task StreamAsync(HttpContext context, AIAgent agent)
    {
        var request = await context.Request.ReadFromJsonAsync<ChatRequest>();
        if (string.IsNullOrWhiteSpace(request?.Message))
        {
            context.Response.StatusCode = 400;
            await context.Response.WriteAsync("missing message");
            return;
        }

        context.Response.Headers.ContentType = "text/event-stream";
        context.Response.Headers.CacheControl = "no-cache";
        context.Response.Headers["X-Accel-Buffering"] = "no";

        using var scope = RequestContext.Scope(
            RequestContext.CurrentUserEmail,
            RequestContext.CurrentUserRole,
            RequestContext.CurrentSessionId,
            request.History ?? new List<HistoryEntry>()
        );

        await foreach (var update in agent.RunStreamingAsync(request.Message))
        {
            if (string.IsNullOrEmpty(update.Text))
            {
                continue;
            }

            var payload = $"data: {update.Text.Replace("\n", "\\n")}\n\n";
            await context.Response.WriteAsync(payload, Encoding.UTF8);
            await context.Response.Body.FlushAsync();
        }
        await context.Response.WriteAsync("event: done\ndata: [DONE]\n\n");
    }
}
