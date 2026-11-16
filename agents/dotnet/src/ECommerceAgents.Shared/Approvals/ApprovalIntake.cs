using Dapper;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Logging;
using ECommerceAgents.Shared.Policy;
using Microsoft.Extensions.Logging;
using Npgsql;
using System.Text.Json;

namespace ECommerceAgents.Shared.Approvals;

/// <summary>
/// The policy decision stored with an approval, as the paused workflow or the tool gate took it. Python's
/// <c>{route, reasons, evidence, policy_version, amount}</c> dict.
/// </summary>
public sealed record RoutedPolicy(
    string Route,
    IReadOnlyList<string> Reasons,
    IReadOnlyDictionary<string, object?> Evidence,
    string PolicyVersion,
    decimal? Amount
);

/// <summary>
/// Opens an inbox <c>approvals</c> row for a gated action, routed by the policy engine — the .NET twin of Python's
/// <c>shared/approvals/intake.py</c>. Two creators exist and each already writes its own execution record: the tool gate
/// (<c>tool_approval_requests</c>) and the paused return workflow (<c>hitl_requests</c>). Those records stay; this adds the
/// inbox row beside them, links the two (<c>approval_id</c>) and records the policy's decision so a reviewer has something to read.
/// </summary>
/// <remarks>
/// A routed approval is created straight in <c>pending_review</c>; an <c>auto</c> route is recorded as such and still waits for a
/// person (acting on it is the customer-confirm step, which is not part of this port). Callers treat a failure here as best
/// effort: the legacy record is what execution needs, and the inbox row is repaired lazily when someone first decides.
/// </remarks>
public sealed class ApprovalIntake(
    DatabasePool pool,
    ApprovalRepository repo,
    ApprovalPolicy? policy,
    ILogger? logger = null)
{
    /// <summary>The role that decides each route. <c>auto</c> is not yet executed by policy, so a person (any staff) decides it.</summary>
    public static readonly IReadOnlyDictionary<string, string> AssignedRole = new Dictionary<string, string>
    {
        ["auto"] = "support",
        ["support"] = "support",
        ["manager"] = "admin",
    };

    /// <summary>Rank of a staff role, lowest first; a role may decide anything assigned at or below its own rank.</summary>
    public static readonly IReadOnlyDictionary<string, int> RoleRank = new Dictionary<string, int>
    {
        ["support"] = 1,
        ["admin"] = 2,
    };

    public static int Rank(string? role) =>
        role is not null && RoleRank.TryGetValue(role.ToLowerInvariant(), out var rank) ? rank : 0;

    private const string FallbackReason = "Policy engine unavailable: routed to a manager review";

    /// <summary>
    /// Used when the engine cannot decide (no facts, unknown kind, no policy file, no database): fail closed to a manager. Not
    /// <c>support</c>: the lowest staff tier must never be the default for a decision nobody evaluated.
    /// </summary>
    public static PolicyDecision FallbackDecision() => new(
        "manager", [FallbackReason], "policy-unavailable", new Dictionary<string, object?>(), "");

    private static RoutedPolicy Recommendation(PolicyDecision d, decimal? amount) =>
        new(d.Route, d.Reasons, d.Evidence, d.Version, amount);

    // ───────────────────────────── gated tool names ─────────────────────────────

    /// <summary>
    /// The policy/Python name of a gated tool: <c>CancelOrder</c> and <c>cancel_order</c> both become <c>cancel_order</c>. The .NET
    /// function pipeline reports PascalCase names; the policy file, the executor and the Python stack all use snake_case, and the
    /// two stacks share the <c>tool_approval_requests</c> table.
    /// </summary>
    public static string CanonicalToolName(string toolName) =>
        string.IsNullOrEmpty(toolName) ? toolName : JsonNamingPolicy.SnakeCaseLower.ConvertName(toolName);

    /// <summary>A tool input mapped to snake_case keys; <see cref="AmbiguousKeys"/> is non-empty when two different keys collapsed onto one name.</summary>
    public sealed record CanonicalInput(JsonElement Input, IReadOnlyCollection<string>? AmbiguousKeys);

    /// <summary>
    /// The top-level keys of a stored tool input in snake_case, the same mapping <see cref="CanonicalToolName"/> applies to the name
    /// (<c>orderId</c> and <c>order_id</c> both become <c>order_id</c>); the gate writes new rows that way already, so only rows stored
    /// before it did are changed. Nested objects are left alone. When two different keys collapse onto one name with different values
    /// the mapping would be a guess: nothing is returned as the input and the colliding keys are listed in <see cref="CanonicalInput.AmbiguousKeys"/>.
    /// </summary>
    public static CanonicalInput CanonicalToolInput(JsonElement input)
    {
        if (input.ValueKind != JsonValueKind.Object) return new CanonicalInput(input, null);
        var map = new Dictionary<string, JsonElement>();
        var ambiguous = new SortedSet<string>(StringComparer.Ordinal);
        foreach (var property in input.EnumerateObject())
        {
            var key = JsonNamingPolicy.SnakeCaseLower.ConvertName(property.Name);
            if (map.TryGetValue(key, out var existing))
            {
                if (existing.GetRawText() != property.Value.GetRawText()) ambiguous.Add(key);
                continue;
            }
            map[key] = property.Value;
        }
        return ambiguous.Count > 0
            ? new CanonicalInput(input, ambiguous)
            : new CanonicalInput(JsonSerializer.SerializeToElement(map), null);
    }

    // ───────────────────────────── deciding the route ─────────────────────────────

    /// <summary>Evaluates the policy for <paramref name="kind"/>; falls back to a manager review if it cannot decide.</summary>
    public async Task<PolicyDecision> DecideRouteAsync(string kind, string? orderId, double? amount, string subjectEmail)
    {
        try
        {
            if (policy is null) throw new PolicyException("no approval policy loaded");
            var facts = await ApprovalFacts.GatherAsync(pool, subjectEmail, orderId, amount);
            return policy.Evaluate(kind, facts);
        }
        catch (Exception ex) when (ex is PolicyException or NpgsqlException or ArgumentException or InvalidOperationException)
        {
            logger?.LogWarning("approval policy could not decide kind={Kind}", LogSafe.Clean(kind));
            return FallbackDecision();
        }
    }

    /// <summary>The policy decision for a paused return, as <see cref="OpenWorkflowApprovalAsync"/> takes it.</summary>
    public async Task<RoutedPolicy> PolicyForReturnAsync(string userEmail, string? orderId, decimal? amount)
    {
        var decision = await DecideRouteAsync("return_approval", orderId, amount is null ? null : (double)amount, userEmail);
        return Recommendation(decision, amount);
    }

    private static DateTime? DueAt(ApprovalRepository repo, string kind) => repo.Deadline(kind, DateTime.UtcNow);

    private static Dictionary<string, object?> RecommendationJson(RoutedPolicy p) => new()
    {
        ["route"] = p.Route,
        ["reasons"] = p.Reasons,
        ["evidence"] = p.Evidence,
        ["policy_version"] = p.PolicyVersion,
    };

    private async Task<Guid?> ExistingConversationAsync(string? candidate)
    {
        if (string.IsNullOrWhiteSpace(candidate)) return null;
        await using var conn = await pool.OpenAsync();
        return await conn.ExecuteScalarAsync<Guid?>("SELECT id FROM conversations WHERE id::text = @candidate", new { candidate });
    }

    /// <summary>Creates the routed approval (<c>pending_review</c>) and its audit events. Idempotent per source record.</summary>
    private async Task<Approval> OpenApprovalAsync(
        string source, Guid sourceRef, string kind, string requesterEmail, RoutedPolicy routed, decimal? amount, string? conversationId)
    {
        var existing = await repo.FindBySourceAsync(source, sourceRef);
        if (existing is not null) return existing;

        var route = AssignedRole.ContainsKey(routed.Route) ? routed.Route : "manager";
        var approval = await repo.CreateAsync(new NewApproval(
            Source: source,
            Kind: kind,
            RequesterEmail: requesterEmail,
            SourceRef: sourceRef,
            SubjectEmail: requesterEmail,
            ConversationId: await ExistingConversationAsync(conversationId),
            Amount: amount is null ? null : Math.Round(amount.Value, 2, MidpointRounding.AwayFromZero),
            PolicyVersion: string.IsNullOrEmpty(routed.PolicyVersion) ? null : routed.PolicyVersion,
            Route: route,
            AssignedRole: AssignedRole[route],
            Status: ApprovalStatus.PendingReview,
            Recommendation: RecommendationJson(routed with { Route = route }),
            DueAt: DueAt(repo, kind)));
        await repo.AppendEventAsync(
            approval.Id,
            "routed",
            payload: new Dictionary<string, object?>
            {
                ["route"] = route,
                ["reasons"] = routed.Reasons,
                ["policy_version"] = routed.PolicyVersion,
            });
        return approval;
    }

    // ───────────────────────────── tool approvals ─────────────────────────────

    /// <summary>The order a gated tool call is about and, for a refund, the amount — read from the requester's own rows.</summary>
    private async Task<(string? OrderId, decimal? Amount)> ToolContextAsync(
        string toolName, JsonElement toolInput, string email)
    {
        string? Str(string key) =>
            toolInput.ValueKind == JsonValueKind.Object && toolInput.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.String
                ? v.GetString()
                : null;

        if (toolName == "process_refund" && Str("return_id") is { } returnId)
        {
            await using var conn = await pool.OpenAsync();
            var row = await conn.QueryFirstOrDefaultAsync(
                @"SELECT r.order_id, r.refund_amount FROM returns r
                    JOIN users u ON u.id = r.user_id
                   WHERE r.id::text = @returnId AND lower(u.email) = lower(@email)",
                new { returnId, email });
            return row is null ? (null, null) : (((Guid)row.order_id).ToString(), (decimal?)row.refund_amount);
        }
        return (Str("order_id"), null);
    }

    /// <summary>The inbox row for a <c>tool_approval_requests</c> row, linked back through <c>approval_id</c>.</summary>
    public async Task<Approval> OpenToolApprovalAsync(
        Guid requestId, string userEmail, string? sessionId, string toolName, JsonElement toolInput)
    {
        var kind = CanonicalToolName(toolName);
        var (orderId, amount) = await ToolContextAsync(kind, toolInput, userEmail);
        var decision = await DecideRouteAsync(kind, orderId, amount is null ? null : (double)amount, userEmail);
        decimal? factsAmount = decision.Evidence.TryGetValue("facts", out var f)
            && f is IReadOnlyDictionary<string, object?> facts
            && facts.TryGetValue("amount", out var a) && a is double d
                ? (decimal)d
                : amount;

        var approval = await OpenApprovalAsync(
            "tool", requestId, kind, userEmail, Recommendation(decision, factsAmount), factsAmount, sessionId);
        await using var conn = await pool.OpenAsync();
        await conn.ExecuteAsync(
            "UPDATE tool_approval_requests SET approval_id = @approvalId WHERE id = @requestId AND approval_id IS NULL",
            new { requestId, approvalId = approval.Id });
        return approval;
    }

    // ───────────────────────────── workflow approvals ─────────────────────────────

    /// <summary>The inbox row for a paused <c>hitl_requests</c> row; <paramref name="policy"/> is the decision the workflow's gate took.</summary>
    public async Task<Approval> OpenWorkflowApprovalAsync(
        Guid hitlRequestId, string userEmail, string? conversationId, RoutedPolicy policy)
    {
        // An absent or unknown route fails closed to the top tier, never the lowest.
        var route = AssignedRole.ContainsKey(policy.Route) ? policy.Route : "manager";
        var reasons = policy.Reasons.Count > 0 ? policy.Reasons : [FallbackReason];
        var routed = policy with { Route = route, Reasons = reasons };
        var approval = await OpenApprovalAsync(
            "workflow", hitlRequestId, "return_approval", userEmail, routed, policy.Amount, conversationId);
        await using var conn = await pool.OpenAsync();
        await conn.ExecuteAsync(
            "UPDATE hitl_requests SET approval_id = @approvalId WHERE id = @hitlRequestId AND approval_id IS NULL",
            new { hitlRequestId, approvalId = approval.Id });
        return approval;
    }
}
