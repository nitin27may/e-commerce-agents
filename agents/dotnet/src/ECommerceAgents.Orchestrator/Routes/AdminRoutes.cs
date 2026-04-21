using Dapper;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Routing;

namespace ECommerceAgents.Orchestrator.Routes;

/// <summary>
/// <c>/api/admin/*</c> endpoints — requires <c>admin</c> role from
/// the stamped <see cref="RequestContext"/>. Covers access-request
/// approvals, aggregate usage stats, and the audit log.
/// </summary>
public static class AdminRoutes
{
    public sealed record AdminActionBody(string AdminNotes = "");

    public static IEndpointRouteBuilder MapAdminRoutes(this IEndpointRouteBuilder routes)
    {
        routes.MapGet("/api/admin/requests", ListPending);
        routes.MapPost("/api/admin/requests/{requestId}/approve", Approve);
        routes.MapPost("/api/admin/requests/{requestId}/deny", Deny);
        routes.MapGet("/api/admin/usage", GetUsage);
        routes.MapGet("/api/admin/audit", GetAudit);
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

    // ─────────────────────── pending ─────────────────────────

    private static async Task<IResult> ListPending(DatabasePool pool)
    {
        var guard = RequireAdmin();
        if (guard is not null) return guard;

        await using var conn = await pool.OpenAsync();
        var rows = (await conn.QueryAsync(
            @"SELECT ar.id, ar.agent_name, ar.role_requested, ar.use_case,
                     ar.status, ar.created_at,
                     u.email, u.name AS user_name, u.role AS user_role
              FROM access_requests ar
              JOIN users u ON ar.user_id = u.id
              WHERE ar.status = 'pending'
              ORDER BY ar.created_at ASC"
        )).Select(r => new
        {
            id = ((Guid)r.id).ToString(),
            agent_name = (string)r.agent_name,
            role_requested = (string?)r.role_requested,
            use_case = (string?)r.use_case,
            status = (string)r.status,
            created_at = ((DateTime)r.created_at).ToString("o"),
            user_email = (string)r.email,
            user_name = (string?)r.user_name,
            user_role = (string?)r.user_role,
        }).ToList();

        return Results.Ok(rows);
    }

    // ─────────────────────── approve ─────────────────────────

    private static async Task<IResult> Approve(
        string requestId,
        [FromBody] AdminActionBody body,
        DatabasePool pool
    )
    {
        var guard = RequireAdmin();
        if (guard is not null) return guard;
        if (!Guid.TryParse(requestId, out var reqId))
        {
            return Results.NotFound(new { detail = "Request not found" });
        }

        var adminId = await UserResolver.ResolveUserIdAsync(pool, RequestContext.CurrentUserEmail);
        if (adminId is null) return Results.Unauthorized();

        await using var conn = await pool.OpenAsync();
        var req = await conn.QueryFirstOrDefaultAsync(
            "SELECT id, user_id, agent_name, role_requested, status FROM access_requests WHERE id = @id",
            new { id = reqId }
        );
        if (req is null)
        {
            return Results.NotFound(new { detail = "Request not found" });
        }

        var status = (string)req.status;
        if (status != "pending")
        {
            return Results.Conflict(new { detail = $"Request already {status}" });
        }

        await using var tx = await conn.BeginTransactionAsync();
        await conn.ExecuteAsync(
            @"UPDATE access_requests
              SET status = 'approved', admin_notes = @notes, reviewed_by = @a, resolved_at = NOW()
              WHERE id = @id",
            new { notes = body?.AdminNotes ?? "", a = adminId, id = reqId },
            tx
        );
        await conn.ExecuteAsync(
            @"INSERT INTO agent_permissions (user_id, agent_name, role, granted_by)
              VALUES (@u, @n, @r, @a)
              ON CONFLICT (user_id, agent_name)
              DO UPDATE SET role = @r, granted_by = @a",
            new
            {
                u = (Guid)req.user_id,
                n = (string)req.agent_name,
                r = (string)req.role_requested,
                a = adminId,
            },
            tx
        );
        await tx.CommitAsync();

        return Results.Ok(new { status = "approved", request_id = reqId.ToString() });
    }

    // ─────────────────────── deny ────────────────────────────

    private static async Task<IResult> Deny(
        string requestId,
        [FromBody] AdminActionBody body,
        DatabasePool pool
    )
    {
        var guard = RequireAdmin();
        if (guard is not null) return guard;
        if (!Guid.TryParse(requestId, out var reqId))
        {
            return Results.NotFound(new { detail = "Request not found" });
        }
        var adminId = await UserResolver.ResolveUserIdAsync(pool, RequestContext.CurrentUserEmail);
        if (adminId is null) return Results.Unauthorized();

        await using var conn = await pool.OpenAsync();
        var req = await conn.QueryFirstOrDefaultAsync(
            "SELECT id, status, agent_name FROM access_requests WHERE id = @id",
            new { id = reqId }
        );
        if (req is null)
        {
            return Results.NotFound(new { detail = "Request not found" });
        }
        var status = (string)req.status;
        if (status != "pending")
        {
            return Results.Conflict(new { detail = $"Request already {status}" });
        }

        await conn.ExecuteAsync(
            @"UPDATE access_requests
              SET status = 'denied', admin_notes = @notes, reviewed_by = @a, resolved_at = NOW()
              WHERE id = @id",
            new { notes = body?.AdminNotes ?? "", a = adminId, id = reqId }
        );

        return Results.Ok(new { status = "denied", request_id = reqId.ToString() });
    }

    // ─────────────────────── usage ───────────────────────────

    private static async Task<IResult> GetUsage(DatabasePool pool)
    {
        var guard = RequireAdmin();
        if (guard is not null) return guard;

        await using var conn = await pool.OpenAsync();
        var overall = await conn.QueryFirstOrDefaultAsync(
            @"SELECT
                  COUNT(*) AS total_requests,
                  COUNT(DISTINCT user_id) AS unique_users,
                  SUM(tokens_in) AS total_tokens_in,
                  SUM(tokens_out) AS total_tokens_out,
                  AVG(duration_ms)::int AS avg_duration_ms,
                  SUM(tool_calls_count) AS total_tool_calls
              FROM usage_logs
              WHERE created_at >= NOW() - INTERVAL '30 days'"
        );

        var byAgent = (await conn.QueryAsync(
            @"SELECT
                  agent_name,
                  COUNT(*) AS request_count,
                  COUNT(DISTINCT user_id) AS unique_users,
                  SUM(tokens_in) AS tokens_in,
                  SUM(tokens_out) AS tokens_out,
                  AVG(duration_ms)::int AS avg_duration_ms,
                  COUNT(*) FILTER (WHERE status = 'error') AS error_count
              FROM usage_logs
              WHERE created_at >= NOW() - INTERVAL '30 days'
              GROUP BY agent_name
              ORDER BY request_count DESC"
        )).Select(r => new
        {
            agent_name = (string)r.agent_name,
            request_count = Convert.ToInt64(r.request_count),
            unique_users = Convert.ToInt64(r.unique_users),
            tokens_in = r.tokens_in is null ? 0L : Convert.ToInt64(r.tokens_in),
            tokens_out = r.tokens_out is null ? 0L : Convert.ToInt64(r.tokens_out),
            avg_duration_ms = r.avg_duration_ms is null ? 0 : Convert.ToInt32(r.avg_duration_ms),
            error_count = Convert.ToInt64(r.error_count),
        }).ToList();

        var daily = (await conn.QueryAsync(
            @"SELECT
                  DATE(created_at) AS day,
                  COUNT(*) AS request_count,
                  COUNT(DISTINCT user_id) AS unique_users
              FROM usage_logs
              WHERE created_at >= NOW() - INTERVAL '7 days'
              GROUP BY DATE(created_at)
              ORDER BY day DESC"
        )).Select(r => new
        {
            day = ((DateTime)r.day).ToString("yyyy-MM-dd"),
            request_count = Convert.ToInt64(r.request_count),
            unique_users = Convert.ToInt64(r.unique_users),
        }).ToList();

        return Results.Ok(new
        {
            period = "last_30_days",
            overall = new
            {
                total_requests = overall is null ? 0L : Convert.ToInt64(overall.total_requests),
                unique_users = overall is null ? 0L : Convert.ToInt64(overall.unique_users),
                total_tokens_in = overall?.total_tokens_in is null ? 0L : Convert.ToInt64(overall.total_tokens_in),
                total_tokens_out = overall?.total_tokens_out is null ? 0L : Convert.ToInt64(overall.total_tokens_out),
                avg_duration_ms = overall?.avg_duration_ms is null ? 0 : Convert.ToInt32(overall.avg_duration_ms),
                total_tool_calls = overall?.total_tool_calls is null ? 0L : Convert.ToInt64(overall.total_tool_calls),
            },
            by_agent = byAgent,
            daily_trend = daily,
        });
    }

    // ─────────────────────── audit ───────────────────────────

    private static async Task<IResult> GetAudit(
        DatabasePool pool,
        int limit = 50,
        int offset = 0
    )
    {
        var guard = RequireAdmin();
        if (guard is not null) return guard;

        int clampedLimit = Math.Clamp(limit, 1, 200);
        int clampedOffset = Math.Max(0, offset);

        await using var conn = await pool.OpenAsync();
        var rows = (await conn.QueryAsync(
            @"SELECT
                  ul.id, ul.agent_name, ul.input_summary, ul.tokens_in, ul.tokens_out,
                  ul.tool_calls_count, ul.duration_ms, ul.status, ul.error_message,
                  ul.trace_id, ul.created_at,
                  u.email AS user_email, u.name AS user_name
              FROM usage_logs ul
              LEFT JOIN users u ON ul.user_id = u.id
              ORDER BY ul.created_at DESC
              LIMIT @limit OFFSET @offset",
            new { limit = clampedLimit, offset = clampedOffset }
        )).ToList();

        var entries = new List<object>();
        foreach (var r in rows)
        {
            var steps = (await conn.QueryAsync(
                @"SELECT step_index, tool_name, tool_input, tool_output, status, duration_ms
                  FROM agent_execution_steps
                  WHERE usage_log_id = @id
                  ORDER BY step_index",
                new { id = (Guid)r.id }
            )).Select(s => new
            {
                step_index = Convert.ToInt32(s.step_index),
                tool_name = (string?)s.tool_name,
                tool_input = (string?)s.tool_input?.ToString(),
                tool_output = (string?)s.tool_output?.ToString(),
                status = (string?)s.status,
                duration_ms = s.duration_ms is null ? (int?)null : Convert.ToInt32(s.duration_ms),
            }).ToList();

            entries.Add(new
            {
                id = ((Guid)r.id).ToString(),
                agent_name = (string?)r.agent_name,
                user_email = (string?)r.user_email,
                user_name = (string?)r.user_name,
                input_summary = (string?)r.input_summary,
                tokens_in = r.tokens_in is null ? 0 : Convert.ToInt32(r.tokens_in),
                tokens_out = r.tokens_out is null ? 0 : Convert.ToInt32(r.tokens_out),
                tool_calls_count = r.tool_calls_count is null ? 0 : Convert.ToInt32(r.tool_calls_count),
                duration_ms = r.duration_ms is null ? (int?)null : Convert.ToInt32(r.duration_ms),
                status = (string?)r.status,
                error_message = (string?)r.error_message,
                trace_id = (string?)r.trace_id,
                created_at = ((DateTime)r.created_at).ToString("o"),
                steps,
            });
        }

        var total = await conn.ExecuteScalarAsync<long>("SELECT COUNT(*) FROM usage_logs");

        return Results.Ok(new
        {
            entries,
            total,
            limit = clampedLimit,
            offset = clampedOffset,
        });
    }
}
