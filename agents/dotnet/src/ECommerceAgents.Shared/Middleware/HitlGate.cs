using Dapper;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using Microsoft.Extensions.Logging;
using System.Text.Json;

namespace ECommerceAgents.Shared.Middleware;

/// <summary>
/// Tool-level human-in-the-loop approval gate — .NET parity port of
/// Python's <c>HITLFunctionMiddleware</c> (<c>shared/hitl.py</c>), now a
/// real interception layer (issue #17, piece 2 of 3) rather than a
/// call-site wrapper each gated tool had to invoke itself
/// (<c>HitlApprovalMiddleware.GuardAsync</c>, removed by this change).
/// </summary>
/// <remarks>
/// Wired into <see cref="Agents.SpecialistPipeline"/>'s function-invocation
/// seam, the same one <c>ToolAuditMiddleware</c> and <c>OutputSanitizer</c>
/// already use — the "generic pipeline stage can't express a per-tool
/// pendingResult shape" objection the old call-site design was built
/// around doesn't actually hold: Python's own middleware doesn't preserve
/// each gated tool's typed result shape for the pending case either — it
/// returns one generic <c>{status, message, request_id}</c> shape for all
/// five gated tools (<c>hitl.py</c> lines ~123-131). This mirrors that
/// exactly rather than trying to synthesize a typed <c>CancelOrderResult</c>
/// / <c>PlaceBackorderResult</c> / etc. generically from
/// <see cref="Microsoft.Extensions.AI.FunctionInvocationContext.Arguments"/>.
///
/// A gated tool method itself (<see cref="ECommerceAgents.OrderManagement.Tools.OrderTools.CancelOrder"/>,
/// <c>ModifyOrder</c>, <see cref="ECommerceAgents.InventoryFulfillment.Tools.InventoryTools.PlaceBackorder"/>)
/// is now only ever reached when this gate lets the call through — it no
/// longer knows or needs to know it's gated. Calling one of those methods
/// directly (as some tests still legitimately do, to exercise its own
/// business logic in isolation) bypasses this gate entirely, since gating
/// now lives at the agent's function-invocation pipeline layer, not inside
/// the method body — the same trade-off every other pipeline stage
/// (audit, sanitization, step recording) already has.
/// </remarks>
public sealed class HitlGate
{
    /// <summary>Matches Python's <c>HITL_GATED_TOOLS</c> — the same 3 the
    /// .NET tool surface actually has; Python's other two
    /// (<c>process_refund</c>, <c>initiate_return</c>) have no .NET tool at
    /// all (no returns specialist), so there's nothing to gate for them here.</summary>
    public static readonly IReadOnlySet<string> GatedTools = new HashSet<string>
    {
        "CancelOrder",
        "ModifyOrder",
        "PlaceBackorder",
    };

    private static readonly IReadOnlyDictionary<string, string> ToolLabels = new Dictionary<string, string>
    {
        ["CancelOrder"] = "cancel order",
        ["ModifyOrder"] = "modify order",
        ["PlaceBackorder"] = "place backorder",
    };

    private readonly DatabasePool _pool;
    private readonly AgentSettings _settings;
    private readonly ILogger<HitlGate>? _logger;

    public HitlGate(DatabasePool pool, AgentSettings settings, ILogger<HitlGate>? logger = null)
    {
        _pool = pool ?? throw new ArgumentNullException(nameof(pool));
        _settings = settings ?? throw new ArgumentNullException(nameof(settings));
        _logger = logger;
    }

    /// <summary>
    /// Returns <c>null</c> if the call should proceed (HITL disabled, or
    /// <paramref name="toolName"/> isn't gated) — otherwise returns the
    /// pending-approval (or, on a DB failure, error) object the caller
    /// should short-circuit with instead of invoking the tool.
    /// </summary>
    public async Task<object?> TryGateAsync(string toolName, string agentName, object toolInputForAudit)
    {
        if (!_settings.HitlEnabled || !GatedTools.Contains(toolName))
        {
            return null;
        }

        var label = ToolLabels.GetValueOrDefault(toolName, toolName);

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
            // Fail CLOSED (bug fix — the call-site wrapper this replaced
            // failed open, contradicting Python's own hitl.py, which fails
            // closed for exactly this reason: a high-stakes tool must never
            // execute unapproved just because the approval record couldn't
            // be written. A transient DB error is not consent.
            _logger?.LogError(ex, "hitl.failed_to_create_request tool={Tool}", toolName);
            return new
            {
                status = "error",
                message = $"Could not submit your {label} request for approval right now — " +
                    "please try again in a moment. No changes have been made.",
            };
        }

        _logger?.LogInformation(
            "hitl.pending tool={Tool} agent={Agent} request_id={RequestId}",
            toolName,
            agentName,
            requestId
        );
        return new
        {
            status = "pending_approval",
            message = $"Your request to {label} has been submitted for manager approval " +
                $"(ref: {requestId.ToString()[..8]}). You will be notified once an admin reviews it. " +
                "No changes have been made yet.",
            request_id = requestId,
        };
    }
}
