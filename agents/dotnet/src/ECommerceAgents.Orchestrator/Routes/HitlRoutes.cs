using Dapper;
using ECommerceAgents.Orchestrator.Approvals;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Logging;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Routing;
using Microsoft.Extensions.Logging;
using System.Text.Json;

namespace ECommerceAgents.Orchestrator.Routes;

/// <summary>
/// <c>/api/admin/hitl/*</c> — the tool-level HITL approval queue admin
/// surface. Mirrors Python's <c>list_hitl_requests</c> /
/// <c>approve_hitl_request</c> / <c>deny_hitl_request</c>. The list stays
/// admin-only; approve and deny are thin shims over the unified
/// <see cref="ApprovalsService.DecideAsync"/> (the same one
/// <c>POST /api/approvals/{id}/decide</c> and the run-resume route call) and
/// are open to staff, bounded by the level the approval policy assigned.
/// <see cref="ECommerceAgents.Shared.Middleware.HitlGate"/> populates the queue.
/// </summary>
public static class HitlRoutes
{
    public sealed record HitlDecisionBody(string? Note = null);

    public static IEndpointRouteBuilder MapHitlRoutes(this IEndpointRouteBuilder routes)
    {
        routes.MapGet("/api/admin/hitl/requests", ListRequests);
        routes.MapPost("/api/admin/hitl/requests/{requestId}/approve", Approve);
        routes.MapPost("/api/admin/hitl/requests/{requestId}/deny", Deny);
        return routes;
    }

    private static IResult? RequireAdmin()
    {
        var email = RequestContext.CurrentUserEmail;
        if (string.IsNullOrEmpty(email)) return Results.Unauthorized();
        if (!string.Equals(RequestContext.CurrentUserRole, "admin", StringComparison.OrdinalIgnoreCase))
        {
            return Results.Json(new { detail = "Admin role required" }, statusCode: 403);
        }
        return null;
    }

    // ─────────────────────── list ────────────────────────────

    private static async Task<IResult> ListRequests(DatabasePool pool, string? status = null, int limit = 50)
    {
        var guard = RequireAdmin();
        if (guard is not null) return guard;

        int clampedLimit = Math.Min(limit, 200);
        var where = string.IsNullOrWhiteSpace(status) ? "" : "WHERE status = @status";

        await using var conn = await pool.OpenAsync();
        var rows = (await conn.QueryAsync(
            $@"SELECT id, user_email, agent_name, tool_name, tool_input,
                      status, admin_note, approved_by, execution_result,
                      created_at, resolved_at
               FROM tool_approval_requests
               {where}
               ORDER BY created_at DESC
               LIMIT @limit",
            new { status, limit = clampedLimit }
        )).Select(r => new
        {
            id = ((Guid)r.id).ToString(),
            user_email = (string)r.user_email,
            agent_name = (string)r.agent_name,
            tool_name = (string)r.tool_name,
            tool_input = ParseJsonObject(r.tool_input),
            status = (string)r.status,
            admin_note = (string?)r.admin_note,
            approved_by = (string?)r.approved_by,
            execution_result = r.execution_result is null ? null : ParseJsonObject(r.execution_result),
            created_at = ((DateTime)r.created_at).ToString("o"),
            resolved_at = r.resolved_at is null ? null : ((DateTime)r.resolved_at).ToString("o"),
        }).ToList();

        return Results.Ok(new { requests = rows, total = rows.Count });
    }

    // ─────────────────────── approve / deny: thin shims over the one decide ───────────────────────

    /// <summary>
    /// Runs a legacy tool-request decision through <see cref="ApprovalsService.DecideAsync"/> — the approvals inbox is the one code path.
    /// This only finds (or, for a request that predates the inbox, creates) the linked approval and maps the service's answers onto the
    /// status codes these routes always returned: 404 unknown, 403 own request, 400 already resolved, 409 lost a race.
    /// Staff, not admin-only: a support reviewer may decide what the policy routed to support, bounded by the assigned level.
    /// </summary>
    private static async Task<(IResult? Failure, DecideOutcome? Outcome)> DecideToolRequestAsync(
        string requestId, ApprovalsService service, ILoggerFactory loggers, string decision, string? note)
    {
        if (StaffGuard.Require() is { } denied) return (denied, null);
        if (!Guid.TryParse(requestId, out var reqId))
        {
            return (Results.NotFound(new { detail = "HITL request not found" }), null);
        }

        var approvalId = await service.EnsureForToolRequestAsync(reqId);
        if (approvalId is null)
        {
            return (Results.NotFound(new { detail = "HITL request not found" }), null);
        }

        var actor = new Actor(RequestContext.CurrentUserEmail, RequestContext.CurrentUserRole);
        try
        {
            var outcome = await service.DecideAsync(approvalId.Value.ToString(), actor, decision, note: note);
            loggers.CreateLogger("hitl").LogInformation(
                "hitl.{Decision} approval_id={ApprovalId}", LogSafe.Clean(decision), approvalId.Value);
            return (null, outcome);
        }
        catch (ApprovalHttpException ex)
        {
            // "already <status>" has always been a 400 here; the unified API answers 409.
            var status = ex.StatusCode == 409 && ex.Detail.StartsWith("Request is already", StringComparison.Ordinal)
                ? 400
                : ex.StatusCode;
            return (Results.Json(new { detail = ex.Detail }, statusCode: status), null);
        }
    }

    private static async Task<IResult> Approve(
        string requestId,
        [FromBody] HitlDecisionBody? body,
        ApprovalsService service,
        ILoggerFactory loggers
    )
    {
        var (failure, outcome) = await DecideToolRequestAsync(requestId, service, loggers, "approve", body?.Note);
        return failure ?? Results.Ok(new { status = "approved", execution_result = outcome!.Result });
    }

    private static async Task<IResult> Deny(
        string requestId,
        [FromBody] HitlDecisionBody? body,
        ApprovalsService service,
        ILoggerFactory loggers
    )
    {
        var (failure, _) = await DecideToolRequestAsync(requestId, service, loggers, "reject", body?.Note);
        return failure ?? Results.Ok(new { status = "denied" });
    }

    // ─────────────────────── helpers ─────────────────────────

    private static JsonElement ParseJson(object? raw)
    {
        var text = raw is string s ? s : raw?.ToString();
        return string.IsNullOrWhiteSpace(text)
            ? JsonDocument.Parse("{}").RootElement
            : JsonDocument.Parse(text).RootElement;
    }

    private static Dictionary<string, JsonElement> ParseJsonObject(object? raw)
    {
        var text = raw is string s ? s : raw?.ToString();
        if (string.IsNullOrWhiteSpace(text)) return new Dictionary<string, JsonElement>();
        try
        {
            return JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(text)
                ?? new Dictionary<string, JsonElement>();
        }
        catch
        {
            return new Dictionary<string, JsonElement>();
        }
    }
}
