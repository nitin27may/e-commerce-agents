using Dapper;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Routing;
using System.Text.Json;

namespace ECommerceAgents.Orchestrator.Routes;

/// <summary>
/// <c>/api/admin/hitl/*</c> — the tool-level HITL approval queue admin
/// surface. Mirrors Python's <c>list_hitl_requests</c> /
/// <c>approve_hitl_request</c> / <c>deny_hitl_request</c>
/// (<c>routes.py:1199-1296</c>, <c>shared/hitl.py</c>). Previously missing
/// entirely from the .NET orchestrator — nothing gated any tool calls, so
/// this queue would always have been empty even if the routes existed;
/// see <see cref="ECommerceAgents.Shared.Middleware.HitlApprovalMiddleware"/>
/// for the piece that actually populates it.
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

    // ─────────────────────── approve ─────────────────────────

    private static async Task<IResult> Approve(
        string requestId,
        [FromBody] HitlDecisionBody? body,
        DatabasePool pool
    )
    {
        var guard = RequireAdmin();
        if (guard is not null) return guard;
        if (!Guid.TryParse(requestId, out var reqId))
        {
            return Results.NotFound(new { detail = "HITL request not found" });
        }

        await using var conn = await pool.OpenAsync();

        // Claim the row BEFORE executing, not after. The previous shape here
        // was: read status, check it's pending, execute, then UPDATE ... WHERE
        // status = 'pending' — and that leaves a real window where two
        // concurrent approvals both pass the pre-check and both execute the
        // underlying action. Only the loser's UPDATE failed, by which point a
        // duplicate refund has already been issued. Python hit exactly this and
        // fixed it the same way; see shared/hitl.py::claim_hitl_request, whose
        // docstring documents the duplicate-refund case as the motivation.
        //
        // A single atomic pending -> processing transition means only one
        // caller ever reaches the executor.
        var claimed = await conn.QueryFirstOrDefaultAsync(
            @"UPDATE tool_approval_requests
              SET status = 'processing'
              WHERE id = @id AND status = 'pending'
              RETURNING id, user_email, tool_name, tool_input",
            new { id = reqId }
        );

        if (claimed is null)
        {
            // Either it doesn't exist, or someone else claimed it first. Tell
            // those apart with a follow-up read purely for the error message —
            // the claim above is what actually guards execution.
            var existing = await conn.QueryFirstOrDefaultAsync(
                "SELECT status FROM tool_approval_requests WHERE id = @id",
                new { id = reqId }
            );
            return existing is null
                ? Results.NotFound(new { detail = "HITL request not found" })
                : Results.BadRequest(new { detail = $"Request is already {(string)existing.status}" });
        }

        var toolInput = ParseJson(claimed.tool_input);
        var result = await HitlActionExecutor.ExecuteAsync(pool, (string)claimed.tool_name, toolInput, (string)claimed.user_email);

        var adminEmail = RequestContext.CurrentUserEmail;
        // Mirrors Python's resolve_hitl_request: execute_approved_action always
        // returns a non-empty dict (even on failure), so "decision == approved
        // and execution_result" is always true here — final DB status is
        // "executed" regardless of whether the underlying action succeeded;
        // that nuance lives only in execution_result.success.
        //
        // Guarded on 'processing' (the state we just claimed) rather than
        // 'pending', so this closes out our own claim and nothing else's.
        await conn.ExecuteAsync(
            @"UPDATE tool_approval_requests
              SET status = 'executed', admin_note = @note, approved_by = @admin,
                  execution_result = @result::jsonb, resolved_at = NOW()
              WHERE id = @id AND status = 'processing'",
            new
            {
                note = body?.Note,
                admin = adminEmail,
                result = JsonSerializer.Serialize(result),
                id = reqId,
            }
        );

        return Results.Ok(new { status = "approved", execution_result = result });
    }

    // ─────────────────────── deny ────────────────────────────

    private static async Task<IResult> Deny(
        string requestId,
        [FromBody] HitlDecisionBody? body,
        DatabasePool pool
    )
    {
        var guard = RequireAdmin();
        if (guard is not null) return guard;
        if (!Guid.TryParse(requestId, out var reqId))
        {
            return Results.NotFound(new { detail = "HITL request not found" });
        }

        await using var conn = await pool.OpenAsync();
        var req = await conn.QueryFirstOrDefaultAsync(
            "SELECT status FROM tool_approval_requests WHERE id = @id",
            new { id = reqId }
        );
        if (req is null)
        {
            return Results.NotFound(new { detail = "HITL request not found" });
        }
        var currentStatus = (string)req.status;
        if (currentStatus != "pending")
        {
            return Results.BadRequest(new { detail = $"Request is already {currentStatus}" });
        }

        var updated = await conn.ExecuteAsync(
            @"UPDATE tool_approval_requests
              SET status = 'denied', admin_note = @note, approved_by = @admin, resolved_at = NOW()
              WHERE id = @id AND status = 'pending'",
            new { note = body?.Note, admin = RequestContext.CurrentUserEmail, id = reqId }
        );
        if (updated == 0)
        {
            return Results.Conflict(new { detail = "Request was already resolved by another admin" });
        }

        return Results.Ok(new { status = "denied" });
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
