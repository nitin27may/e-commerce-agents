using Dapper;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Routing;

namespace ECommerceAgents.Orchestrator.Routes;

/// <summary>
/// <c>/api/marketplace/*</c> endpoints — list agents, submit access
/// requests, view granted agents. Parity with Python.
/// </summary>
public static class MarketplaceRoutes
{
    public sealed record AccessRequestBody(string AgentName, string RoleRequested, string UseCase);

    public static IEndpointRouteBuilder MapMarketplaceRoutes(this IEndpointRouteBuilder routes)
    {
        routes.MapGet("/api/marketplace/agents", ListAgents);
        routes.MapPost("/api/marketplace/request", SubmitRequest);
        routes.MapGet("/api/marketplace/my-agents", ListMyAgents);
        return routes;
    }

    // ─────────────────────── list ────────────────────────────

    private static async Task<IResult> ListAgents(DatabasePool pool)
    {
        var email = RequestContext.CurrentUserEmail;
        if (string.IsNullOrEmpty(email)) return Results.Unauthorized();

        await using var conn = await pool.OpenAsync();
        var rows = (await conn.QueryAsync(
            @"SELECT id, name, display_name, description, category, icon, status,
                     version, capabilities, requires_approval, allowed_roles
              FROM agent_catalog
              WHERE status = 'active'
              ORDER BY display_name"
        )).ToList();

        var result = rows.Select(r => new
        {
            id = ((Guid)r.id).ToString(),
            name = (string)r.name,
            display_name = (string)r.display_name,
            description = (string?)r.description,
            category = (string?)r.category,
            icon = (string?)r.icon,
            status = (string?)r.status,
            version = (string?)r.version,
            capabilities = (r.capabilities as string[])?.ToList() ?? new List<string>(),
            requires_approval = (bool)r.requires_approval,
            allowed_roles = (r.allowed_roles as string[])?.ToList() ?? new List<string>(),
        }).ToList();

        return Results.Ok(result);
    }

    // ─────────────────────── request ─────────────────────────

    private static async Task<IResult> SubmitRequest(
        [FromBody] AccessRequestBody body,
        DatabasePool pool
    )
    {
        if (body is null
            || string.IsNullOrWhiteSpace(body.AgentName)
            || string.IsNullOrWhiteSpace(body.RoleRequested)
            || string.IsNullOrWhiteSpace(body.UseCase))
        {
            return Results.BadRequest(new { detail = "agent_name, role_requested and use_case are required" });
        }

        var userId = await UserResolver.ResolveUserIdAsync(pool, RequestContext.CurrentUserEmail);
        if (userId is null) return Results.Unauthorized();

        await using var conn = await pool.OpenAsync();

        var agent = await conn.QueryFirstOrDefaultAsync(
            "SELECT name, requires_approval FROM agent_catalog WHERE name = @n AND status = 'active'",
            new { n = body.AgentName }
        );
        if (agent is null)
        {
            return Results.NotFound(new { detail = "Agent not found" });
        }

        var existingReq = await conn.QueryFirstOrDefaultAsync<Guid?>(
            @"SELECT id FROM access_requests
              WHERE user_id = @u AND agent_name = @n AND status = 'pending'",
            new { u = userId, n = body.AgentName }
        );
        if (existingReq is not null)
        {
            return Results.Conflict(new { detail = "You already have a pending request for this agent" });
        }

        var existingPerm = await conn.QueryFirstOrDefaultAsync<Guid?>(
            "SELECT id FROM agent_permissions WHERE user_id = @u AND agent_name = @n",
            new { u = userId, n = body.AgentName }
        );
        if (existingPerm is not null)
        {
            return Results.Conflict(new { detail = "You already have access to this agent" });
        }

        bool requiresApproval = (bool)agent.requires_approval;

        if (!requiresApproval)
        {
            await conn.ExecuteAsync(
                @"INSERT INTO agent_permissions (user_id, agent_name, role)
                  VALUES (@u, @n, @r)",
                new { u = userId, n = body.AgentName, r = body.RoleRequested }
            );
            var rowId = await conn.ExecuteScalarAsync<Guid>(
                @"INSERT INTO access_requests (user_id, agent_name, role_requested, use_case, status, resolved_at)
                  VALUES (@u, @n, @r, @c, 'approved', NOW())
                  RETURNING id",
                new { u = userId, n = body.AgentName, r = body.RoleRequested, c = body.UseCase }
            );
            return Results.Ok(new
            {
                id = rowId.ToString(),
                agent_name = body.AgentName,
                status = "approved",
                message = "Access granted automatically — no approval required.",
            });
        }

        var pendingId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO access_requests (user_id, agent_name, role_requested, use_case)
              VALUES (@u, @n, @r, @c)
              RETURNING id",
            new { u = userId, n = body.AgentName, r = body.RoleRequested, c = body.UseCase }
        );

        return Results.Ok(new
        {
            id = pendingId.ToString(),
            agent_name = body.AgentName,
            status = "pending",
            message = "Your request has been submitted and is pending admin approval.",
        });
    }

    // ─────────────────────── my agents ───────────────────────

    private static async Task<IResult> ListMyAgents(DatabasePool pool)
    {
        var userId = await UserResolver.ResolveUserIdAsync(pool, RequestContext.CurrentUserEmail);
        if (userId is null) return Results.Unauthorized();

        await using var conn = await pool.OpenAsync();
        var rows = (await conn.QueryAsync(
            @"SELECT ap.agent_name, ap.role, ap.granted_at,
                     ac.display_name, ac.description, ac.category, ac.icon
              FROM agent_permissions ap
              JOIN agent_catalog ac ON ap.agent_name = ac.name
              WHERE ap.user_id = @u
              ORDER BY ap.granted_at DESC",
            new { u = userId }
        )).Select(r => new
        {
            agent_name = (string)r.agent_name,
            display_name = (string?)r.display_name,
            description = (string?)r.description,
            category = (string?)r.category,
            icon = (string?)r.icon,
            role = (string?)r.role,
            granted_at = ((DateTime)r.granted_at).ToString("o"),
        }).ToList();

        return Results.Ok(rows);
    }
}
