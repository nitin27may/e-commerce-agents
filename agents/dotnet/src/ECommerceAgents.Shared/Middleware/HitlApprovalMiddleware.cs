using Dapper;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using Microsoft.Extensions.Logging;
using System.Text.Json;

namespace ECommerceAgents.Shared.Middleware;

/// <summary>
/// Tool-level human-in-the-loop approval queue. .NET parity port of Python's
/// <c>HITLFunctionMiddleware</c> (<c>shared/hitl.py</c>) — but structured as
/// a call-site wrapper, not a framework-level function-invocation
/// interceptor. MAF .NET 1.18+ does have a real function-invocation
/// interception hook (<c>FunctionInvocationDelegatingAgentBuilderExtensions.Use</c>
/// on <c>AIAgentBuilder</c> — see <see cref="Agents.SpecialistPipeline"/>,
/// which uses it for <see cref="ToolAuditMiddleware"/>), so this class stays
/// a call-site wrapper by choice, not necessity: it needs to short-circuit
/// with a caller-supplied <c>pendingResult</c> shape specific to each gated
/// tool's return type, which a generic pipeline stage can't express as
/// cleanly as the tool body calling <see cref="GuardAsync{T}"/> itself.
/// Follows the same established call-site-wraps-itself pattern as
/// <see cref="ToolAuditMiddleware"/>'s own <c>RecordAsync</c>.
/// </summary>
/// <remarks>
/// When <see cref="AgentSettings.HitlEnabled"/> is on and a gated tool is
/// invoked: a <c>tool_approval_requests</c> row is created with
/// <c>status='pending'</c>, the wrapped tool body is NOT executed, and the
/// caller gets back a "pending approval" result instead — mirrored via the
/// caller-supplied <paramref name="pendingResult"/> factory, since each
/// .NET tool has its own strongly-typed result record (no generic dict
/// shape the way Python's tool results are). The actual DB mutation happens
/// later, when an admin approves the request (see the admin HITL routes,
/// which dispatch through <c>HitlActionExecutor</c>).
/// </remarks>
public sealed class HitlApprovalMiddleware
{
    private readonly DatabasePool _pool;
    private readonly AgentSettings _settings;
    private readonly ILogger<HitlApprovalMiddleware>? _logger;

    public HitlApprovalMiddleware(DatabasePool pool, AgentSettings settings, ILogger<HitlApprovalMiddleware>? logger = null)
    {
        _pool = pool ?? throw new ArgumentNullException(nameof(pool));
        _settings = settings ?? throw new ArgumentNullException(nameof(settings));
        _logger = logger;
    }

    public async Task<T> GuardAsync<T>(
        string toolName,
        string agentName,
        object toolInputForAudit,
        Func<Task<T>> body,
        Func<Guid, T> pendingResult
    )
    {
        if (!_settings.HitlEnabled)
        {
            return await body();
        }

        Guid requestId;
        try
        {
            var email = RequestContext.CurrentUserEmail;
            Guid? sessionId = Guid.TryParse(RequestContext.CurrentSessionId, out var sid) ? sid : null;

            await using var conn = await _pool.OpenAsync();
            requestId = await conn.ExecuteScalarAsync<Guid>(
                @"INSERT INTO tool_approval_requests (user_email, session_id, agent_name, tool_name, tool_input)
                  VALUES (@email, @session, @agent, @tool, @input::jsonb)
                  RETURNING id",
                new
                {
                    email = string.IsNullOrEmpty(email) ? "unknown" : email,
                    session = sessionId,
                    agent = agentName,
                    tool = toolName,
                    input = JsonSerializer.Serialize(toolInputForAudit),
                }
            );
        }
        catch (Exception ex)
        {
            // Fail open — let the tool execute rather than silently failing,
            // matching Python's `except Exception: ... await call_next()`.
            _logger?.LogError(ex, "hitl.failed_to_create_request tool={Tool}", toolName);
            return await body();
        }

        _logger?.LogInformation(
            "hitl.pending tool={Tool} agent={Agent} request_id={RequestId}",
            toolName,
            agentName,
            requestId
        );
        return pendingResult(requestId);
    }
}
