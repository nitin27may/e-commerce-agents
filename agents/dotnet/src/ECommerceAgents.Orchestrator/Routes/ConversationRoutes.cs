using Dapper;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;

namespace ECommerceAgents.Orchestrator.Routes;

/// <summary>
/// <c>/api/conversations</c> endpoints. Parity with Python's
/// <c>orchestrator/routes.py</c> conversation handlers.
/// </summary>
public static class ConversationRoutes
{
    public static IEndpointRouteBuilder MapConversationRoutes(this IEndpointRouteBuilder routes)
    {
        routes.MapGet("/api/conversations", ListConversations);
        routes.MapGet("/api/conversations/{id}", GetConversation);
        routes.MapDelete("/api/conversations/{id}", DeleteConversation);
        return routes;
    }

    private static async Task<IResult> ListConversations(DatabasePool pool)
    {
        var userId = await UserResolver.ResolveUserIdAsync(pool, RequestContext.CurrentUserEmail);
        if (userId is null) return Results.Unauthorized();

        await using var conn = await pool.OpenAsync();
        var rows = await conn.QueryAsync(
            @"SELECT c.id, c.title, c.created_at, c.last_message_at,
                     (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS message_count
              FROM conversations c
              WHERE c.user_id = @uid AND c.is_active = TRUE
              ORDER BY c.last_message_at DESC
              LIMIT 50",
            new { uid = userId }
        );
        return Results.Ok(rows.Select(r => new
        {
            id = ((Guid)r.id).ToString(),
            title = (string?)r.title,
            message_count = Convert.ToInt32(r.message_count),
            created_at = ((DateTime)r.created_at).ToString("o"),
            last_message_at = ((DateTime)r.last_message_at).ToString("o"),
        }));
    }

    private static async Task<IResult> GetConversation(string id, DatabasePool pool)
    {
        if (!Guid.TryParse(id, out var convId))
        {
            return Results.NotFound(new { detail = "Conversation not found" });
        }
        var userId = await UserResolver.ResolveUserIdAsync(pool, RequestContext.CurrentUserEmail);
        if (userId is null) return Results.Unauthorized();

        await using var conn = await pool.OpenAsync();
        var conv = await conn.QueryFirstOrDefaultAsync(
            @"SELECT id, title, created_at, last_message_at
              FROM conversations
              WHERE id = @id AND user_id = @uid AND is_active = TRUE",
            new { id = convId, uid = userId }
        );
        if (conv is null)
        {
            return Results.NotFound(new { detail = "Conversation not found" });
        }

        var messages = (await conn.QueryAsync(
            @"SELECT id, role, content, agent_name, agents_involved, metadata,
                     tokens_in, tokens_out, created_at
              FROM messages
              WHERE conversation_id = @id
              ORDER BY created_at ASC",
            new { id = convId }
        )).Select(m => new
        {
            id = ((Guid)m.id).ToString(),
            role = (string)m.role,
            content = (string)m.content,
            agent_name = (string?)m.agent_name,
            agents_involved = (m.agents_involved as string[])?.ToList() ?? new List<string>(),
            metadata = (m.metadata as string) ?? "{}",
            tokens_in = Convert.ToInt32(m.tokens_in ?? 0),
            tokens_out = Convert.ToInt32(m.tokens_out ?? 0),
            created_at = ((DateTime)m.created_at).ToString("o"),
        }).ToList();

        return Results.Ok(new
        {
            id = ((Guid)conv.id).ToString(),
            title = (string?)conv.title,
            created_at = ((DateTime)conv.created_at).ToString("o"),
            last_message_at = ((DateTime)conv.last_message_at).ToString("o"),
            messages,
        });
    }

    private static async Task<IResult> DeleteConversation(string id, DatabasePool pool)
    {
        if (!Guid.TryParse(id, out var convId))
        {
            return Results.NotFound(new { detail = "Conversation not found" });
        }
        var userId = await UserResolver.ResolveUserIdAsync(pool, RequestContext.CurrentUserEmail);
        if (userId is null) return Results.Unauthorized();

        await using var conn = await pool.OpenAsync();
        var affected = await conn.ExecuteAsync(
            @"UPDATE conversations SET is_active = FALSE
              WHERE id = @id AND user_id = @uid AND is_active = TRUE",
            new { id = convId, uid = userId }
        );

        return affected == 0
            ? Results.NotFound(new { detail = "Conversation not found" })
            : Results.Ok(new { status = "deleted" });
    }
}
