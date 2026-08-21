using Dapper;
using System.Text.Json;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;

namespace ECommerceAgents.Orchestrator.Routes;

/// <summary>
/// <c>GET /api/runs</c> — the caller's own recent agent runs with step
/// details; admins see every user's runs. Mirrors Python's <c>list_runs</c>
/// (<c>routes.py:1406-1491</c>). Previously missing entirely from the .NET
/// orchestrator. Distinct from <c>GET /api/admin/audit</c>
/// (<see cref="AdminRoutes.MapAdminRoutes"/>): that one is admin-only with
/// agent_name/status/search filters and includes <c>error_message</c>; this
/// one is scoped by caller identity, has no filters, and — matching
/// Python — omits <c>error_message</c> from each entry.
/// </summary>
public static class RunsRoutes
{
    public static IEndpointRouteBuilder MapRunsRoutes(this IEndpointRouteBuilder routes)
    {
        routes.MapGet("/api/runs", ListRuns);
        routes.MapGet("/api/runs/{runId}/checkpoints", GetRunCheckpoints);
        return routes;
    }

    /// <summary>
    /// <c>GET /api/runs/{runId}/checkpoints</c> — checkpoints saved during a
    /// run, plus its latest HITL request. Mirrors Python's
    /// <c>get_run_checkpoints</c>.
    /// </summary>
    /// <remarks>
    /// Without this route <c>/runs</c> renders perfectly and its checkpoint and
    /// approval panels stay permanently empty, because the client absorbs the
    /// 404 in a <c>.catch(() =&gt; {})</c> — the page looks healthy and is
    /// quietly incomplete. See issue #33.
    ///
    /// Ownership is checked against <c>usage_logs.user_id</c> rather than just
    /// "does this checkpoint exist", the same way Python scopes it: a
    /// checkpoint payload can carry order and refund details, so who is asking
    /// matters.
    /// </remarks>
    private static async Task<IResult> GetRunCheckpoints(string runId, DatabasePool pool)
    {
        var email = RequestContext.CurrentUserEmail;
        if (string.IsNullOrEmpty(email))
        {
            return Results.Unauthorized();
        }

        if (!Guid.TryParse(runId, out var runGuid))
        {
            return Results.NotFound(new { detail = "Run not found" });
        }

        var isAdmin = string.Equals(RequestContext.CurrentUserRole, "admin", StringComparison.OrdinalIgnoreCase);

        await using var conn = await pool.OpenAsync();

        var exists = isAdmin
            ? await conn.ExecuteScalarAsync<Guid?>(
                "SELECT id FROM usage_logs WHERE id = @id", new { id = runGuid })
            : await conn.ExecuteScalarAsync<Guid?>(
                @"SELECT ul.id FROM usage_logs ul
                  JOIN users u ON ul.user_id = u.id
                  WHERE ul.id = @id AND u.email = @email",
                new { id = runGuid, email });

        if (exists is null)
        {
            return Results.NotFound(new { detail = "Run not found" });
        }

        var checkpoints = (await conn.QueryAsync(
            @"SELECT checkpoint_id, workflow_name, created_at
              FROM workflow_checkpoints
              WHERE usage_log_id = @id
              ORDER BY created_at ASC",
            new { id = runGuid }
        )).Select(r => new
        {
            checkpoint_id = ((Guid)r.checkpoint_id).ToString(),
            workflow_name = (string)r.workflow_name,
            created_at = ((DateTime)r.created_at).ToString("o"),
        }).ToList();

        var hitl = await conn.QueryFirstOrDefaultAsync(
            @"SELECT id, status, payload, response, created_at, responded_at
              FROM hitl_requests WHERE workflow_run_id = @id
              ORDER BY created_at DESC LIMIT 1",
            new { id = runGuid }
        );

        return Results.Ok(new
        {
            run_id = runId,
            checkpoints,
            hitl_request = hitl is null
                ? null
                : (object)new
                {
                    id = ((Guid)hitl.id).ToString(),
                    status = (string)hitl.status,
                    // Npgsql hands JSONB back as a raw string with no codec
                    // registered, so these are re-parsed rather than passed
                    // through — the same guard every other JSONB read here uses.
                    payload = ParseJsonOrNull(hitl.payload),
                    response = ParseJsonOrNull(hitl.response),
                    created_at = ((DateTime)hitl.created_at).ToString("o"),
                    responded_at = hitl.responded_at is null
                        ? null
                        : ((DateTime)hitl.responded_at).ToString("o"),
                },
        });
    }

    private static JsonElement? ParseJsonOrNull(object? raw)
    {
        var text = raw as string ?? raw?.ToString();
        if (string.IsNullOrWhiteSpace(text))
        {
            return null;
        }
        try
        {
            return JsonDocument.Parse(text).RootElement.Clone();
        }
        catch (JsonException)
        {
            return null;
        }
    }

    private static async Task<IResult> ListRuns(DatabasePool pool, int limit = 20, int offset = 0)
    {
        var email = RequestContext.CurrentUserEmail;
        if (string.IsNullOrEmpty(email))
        {
            return Results.Unauthorized();
        }
        var isAdmin = string.Equals(RequestContext.CurrentUserRole, "admin", StringComparison.OrdinalIgnoreCase);

        int clampedLimit = Math.Clamp(limit, 1, 100);
        int clampedOffset = Math.Max(0, offset);
        var where = isAdmin ? "" : "WHERE ul.user_id = (SELECT id FROM users WHERE email = @email)";

        await using var conn = await pool.OpenAsync();
        var rows = (await conn.QueryAsync(
            $@"SELECT ul.id, ul.agent_name, ul.input_summary, ul.tokens_in, ul.tokens_out,
                      ul.tool_calls_count, ul.duration_ms, ul.status, ul.trace_id, ul.created_at,
                      u.email AS user_email, u.name AS user_name
               FROM usage_logs ul
               LEFT JOIN users u ON ul.user_id = u.id
               {where}
               ORDER BY ul.created_at DESC
               LIMIT @limit OFFSET @offset",
            new { email, limit = clampedLimit, offset = clampedOffset }
        )).ToList();

        var entries = new List<object>();
        foreach (var r in rows)
        {
            var steps = await UsageLogSteps.FetchAsync(conn, (Guid)r.id);
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
                trace_id = (string?)r.trace_id,
                created_at = ((DateTime)r.created_at).ToString("o"),
                steps,
            });
        }

        var total = await conn.ExecuteScalarAsync<long>(
            $"SELECT COUNT(*) FROM usage_logs ul {where}",
            new { email }
        );

        return Results.Ok(new { entries, total, limit = clampedLimit, offset = clampedOffset });
    }
}
