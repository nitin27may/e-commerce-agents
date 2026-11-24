using ECommerceAgents.Shared.Logging;
using Dapper;
using ECommerceAgents.Shared.Approvals;
using ECommerceAgents.Shared.Policy;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using Microsoft.Extensions.Logging;
using System.Reflection;
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
    /// <summary>
    /// Matches Python's <c>HITL_GATED_TOOLS</c>, now all five.
    /// </summary>
    /// <remarks>
    /// This used to list three and explain that <c>initiate_return</c> and
    /// <c>process_refund</c> "have no .NET tool at all, so there's nothing to
    /// gate". <see cref="Tools.ReturnTools"/> gives them one, so they are
    /// gated in the same change that introduces them — a refund tool reaching
    /// production ahead of its approval gate is the one ordering mistake this
    /// parity work must not make.
    /// </remarks>
    public static readonly IReadOnlySet<string> GatedTools = new HashSet<string>
    {
        "CancelOrder",
        "ModifyOrder",
        "PlaceBackorder",
        "InitiateReturn",
        "ProcessRefund",
    };

    /// <summary>The gated names in the canonical (snake_case) form the pipeline reports them in: <c>AgentTool.Create</c> registers <c>cancel_order</c>, not <c>CancelOrder</c>.</summary>
    private static readonly IReadOnlySet<string> CanonicalGatedTools =
        GatedTools.Select(ApprovalIntake.CanonicalToolName).ToHashSet(StringComparer.Ordinal);

    /// <summary>The roles a tool declares with <see cref="Guardrails.RequiresRoleAttribute"/>, or <c>null</c> when it declares none.</summary>
    public static IReadOnlyList<string>? RolesOf(Microsoft.Extensions.AI.AIFunction function) =>
        function.UnderlyingMethod?.GetCustomAttribute<Guardrails.RequiresRoleAttribute>()?.Roles;

    /// <summary>What the customer asked for, and the argument that names the subject; word for word Python's <c>_PARK_VERBS</c>.</summary>
    private static readonly IReadOnlyDictionary<string, (string Verb, string Key)> ParkVerbs = new Dictionary<string, (string, string)>
    {
        ["cancel_order"] = ("cancelling order", "order_id"),
        ["modify_order"] = ("changing the shipping address on order", "order_id"),
        ["initiate_return"] = ("starting a return for order", "order_id"),
        ["process_refund"] = ("processing the refund for return", "return_id"),
        ["place_backorder"] = ("placing a backorder for product", "product_id"),
    };

    /// <summary>What the model is told in place of a gated call's outcome: the user has already been given the sentence.</summary>
    public const string HandledResultMessage =
        "This request has been handled and the user has been told the outcome. Do not add, repeat or reword anything about it.";

    /// <summary>The sentence the customer reads for a parked call. Python's <c>parked_reply</c>, word for word.</summary>
    public static string ParkedReply(string canonicalTool, string toolInputJson, Guid? requestId)
    {
        var reference = requestId is { } id ? $" (ref: {id.ToString()[..8]})" : "";
        var (verb, key) = ParkVerbs.TryGetValue(canonicalTool, out var found) ? found : (canonicalTool.Replace('_', ' '), "");
        var subject = "";
        if (key.Length > 0)
        {
            try
            {
                using var doc = JsonDocument.Parse(toolInputJson);
                if (doc.RootElement.ValueKind == JsonValueKind.Object && doc.RootElement.TryGetProperty(key, out var value))
                {
                    subject = (value.ValueKind == JsonValueKind.String ? value.GetString() : value.ToString())?.Trim() ?? "";
                }
            }
            catch (JsonException)
            {
                // No subject; the sentence still reads correctly without it.
            }
        }

        var what = subject.Length > 0 ? $"{verb} {subject}" : verb;
        return $"I have asked a team member to approve {what}{reference}. Nothing has changed yet; you will be notified once it has been reviewed.";
    }

    /// <summary>
    /// The pipeline's side of a gated call: records the sentence <paramref name="gateResult"/> carries for the host to append, and
    /// returns the short neutral result the model sees instead. Falls back to the original result when no sink is active, so a
    /// caller outside a request scope still gets the sentence through the model rather than losing it.
    /// </summary>
    public static object ForModel(object gateResult)
    {
        var element = JsonSerializer.SerializeToElement(gateResult);
        if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty("message", out var message) || message.ValueKind != JsonValueKind.String)
        {
            return gateResult;
        }

        var sentence = message.GetString();
        if (string.IsNullOrEmpty(sentence) || !RequestContext.RecordGateNotice(sentence))
        {
            return gateResult;
        }

        var status = element.TryGetProperty("status", out var s) && s.ValueKind == JsonValueKind.String ? s.GetString() : "handled";
        return new { status, message = HandledResultMessage };
    }

    private static object FailedClosed() => new { status = "error", message = ParkPrechecks.ParkFailedReply };

    private readonly DatabasePool _pool;
    private readonly AgentSettings _settings;
    private readonly ILogger<HitlGate>? _logger;
    private readonly ApprovalIntake _intake;
    private readonly CustomerConfirmationRepository _confirmations;

    /// <param name="intake">
    /// Opens the inbox <c>approvals</c> row beside each pending request. Defaults to one built over the same pool and the shared
    /// <c>approvals.yaml</c>; a test passes its own to control the policy.
    /// </param>
    /// <param name="confirmations">
    /// The customer's own confirmation record (#307). Defaults to one over the same pool; a test passes its own only to share a pool.
    /// </param>
    public HitlGate(
        DatabasePool pool,
        AgentSettings settings,
        ILogger<HitlGate>? logger = null,
        ApprovalIntake? intake = null,
        CustomerConfirmationRepository? confirmations = null)
    {
        _pool = pool ?? throw new ArgumentNullException(nameof(pool));
        _settings = settings ?? throw new ArgumentNullException(nameof(settings));
        _logger = logger;
        _intake = intake ?? new ApprovalIntake(
            pool,
            new ApprovalRepository(pool, settings, ApprovalPolicyLocator.Current),
            ApprovalPolicyLocator.Current);
        _confirmations = confirmations ?? new CustomerConfirmationRepository(pool, settings, logger);
    }

    /// <summary>
    /// The tool input as the shared table stores it: top-level keys in snake_case (<c>orderId</c> becomes <c>order_id</c>), which is
    /// what the executor, the policy and Python read. The function pipeline reports .NET parameter names.
    /// </summary>
    internal static string SnakeCaseInput(object toolInput)
    {
        var element = JsonSerializer.SerializeToElement(toolInput);
        if (element.ValueKind != JsonValueKind.Object) return element.GetRawText();
        var map = new Dictionary<string, JsonElement>();
        foreach (var property in element.EnumerateObject())
        {
            map[JsonNamingPolicy.SnakeCaseLower.ConvertName(property.Name)] = property.Value;
        }
        return JsonSerializer.Serialize(map);
    }

    /// <summary>
    /// Returns <c>null</c> if the call should proceed (HITL disabled, or
    /// <paramref name="toolName"/> isn't gated) — otherwise returns the
    /// refusal, pending-approval (or, on a failure, error) object the caller
    /// should short-circuit with instead of invoking the tool.
    /// </summary>
    /// <remarks>
    /// Before anything is written, <see cref="ParkPrechecks"/> runs the tool's declared roles (<paramref name="requiredRoles"/>) and
    /// its cheap preconditions (ownership, status, window, stock): a call that can never succeed gets a plain sentence and no row,
    /// as in Python. No identity, or a failure in a precheck or the write, fails closed with <see cref="ParkPrechecks.ParkFailedReply"/>.
    /// </remarks>
    public async Task<object?> TryGateAsync(string toolName, string agentName, object toolInputForAudit, IReadOnlyList<string>? requiredRoles = null)
    {
        var canonicalTool = ApprovalIntake.CanonicalToolName(toolName);
        if (!_settings.HitlEnabled || !CanonicalGatedTools.Contains(canonicalTool))
        {
            return null;
        }

        Guid requestId;
        Guid? confirmationId = null;
        string toolInputJson;
        string requester;
        Guid? sessionGuid;
        try
        {
            var email = RequestContext.CurrentUserEmail;
            if (string.IsNullOrEmpty(email))
            {
                _logger?.LogWarning("hitl.park_refused reason=no_identity tool={Tool}", LogSafe.Clean(toolName));
                return FailedClosed();
            }

            Guid? sessionId = Guid.TryParse(RequestContext.CurrentSessionId, out var sid) ? sid : null;
            requester = email;
            sessionGuid = sessionId;
            toolInputJson = SnakeCaseInput(toolInputForAudit);

            using (var inputDoc = JsonDocument.Parse(toolInputJson))
            {
                var refusal = await ParkPrechecks.CheckAsync(
                    _pool, _settings, canonicalTool, inputDoc.RootElement, email, RequestContext.CurrentUserRole, requiredRoles, _logger);
                if (refusal is not null)
                {
                    return new { status = "refused", message = refusal.Sentence };
                }
            }

            // The customer confirms the exact action first (not an approval: no inbox row). The first request for a call only opens the
            // question; it parks when the same call arrives again after the user's own explicit yes.
            if (_settings.CustomerConfirmationEnabled)
            {
                using var inputDoc = JsonDocument.Parse(toolInputJson);
                var confirmation = await _confirmations.EvaluateAsync(
                    email, RequestContext.CurrentSessionId, canonicalTool, inputDoc.RootElement.Clone(), RequestContext.CurrentTurnMessage);
                confirmationId = confirmation.ConfirmationId;
                if (confirmation.Kind is ConfirmationKind.Ask or ConfirmationKind.Declined)
                {
                    var asked = confirmation.Kind == ConfirmationKind.Ask;
                    _logger?.LogInformation(
                        "hitl.confirmation tool={Tool} outcome={Outcome}", LogSafe.Clean(toolName), asked ? "ask" : "declined");
                    return new
                    {
                        status = asked ? "confirmation_required" : "declined",
                        message = confirmation.Sentence ?? ParkPrechecks.ParkFailedReply,
                    };
                }
            }

            await using var conn = await _pool.OpenAsync();
            requestId = await conn.ExecuteScalarAsync<Guid>(
                @"INSERT INTO tool_approval_requests (user_email, session_id, agent_name, tool_name, tool_input)
                  VALUES (@email, @session, @agent, @tool, @input::jsonb)
                  RETURNING id",
                new
                {
                    email = requester,
                    session = sessionId,
                    agent = agentName,
                    // snake_case, as Python stores it: the policy kinds, the executor and the other stack's approve path key on it.
                    tool = canonicalTool,
                    input = toolInputJson,
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
            _logger?.LogError(ex, "hitl.failed_to_create_request tool={Tool}", LogSafe.Clean(toolName));
            // The customer's yes should still stand if the park could not be written.
            if (confirmationId is { } consumed)
            {
                await _confirmations.ReopenAsync(consumed);
            }

            return FailedClosed();
        }

        // The inbox row, routed by the policy engine. Best effort: the request above is what executing it needs, and a missing inbox
        // row is repaired when someone first decides (ApprovalsService.EnsureForToolRequestAsync). It must never change what the
        // caller returns, so a failure is logged (ids only) and swallowed.
        try
        {
            using var input = JsonDocument.Parse(toolInputJson);
            await _intake.OpenToolApprovalAsync(requestId, requester, sessionGuid?.ToString(), canonicalTool, input.RootElement.Clone());
        }
        catch (Exception ex)
        {
            _logger?.LogError(ex, "hitl.approval_open_failed request_id={RequestId}", requestId);
        }

        _logger?.LogInformation(
            "hitl.pending tool={Tool} agent={Agent} request_id={RequestId}",
            LogSafe.Clean(toolName),
            LogSafe.Clean(agentName),
            LogSafe.Clean(requestId)
        );
        return new
        {
            status = "pending_approval",
            message = ParkedReply(canonicalTool, toolInputJson, requestId),
            request_id = requestId,
        };
    }
}
