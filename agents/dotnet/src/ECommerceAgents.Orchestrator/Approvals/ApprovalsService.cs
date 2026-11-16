using Dapper;
using ECommerceAgents.Orchestrator.Modes;
using ECommerceAgents.Orchestrator.Routes;
using ECommerceAgents.Shared.Approvals;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Logging;
using ECommerceAgents.Shared.Orchestration;
using ECommerceAgents.Shared.Policy;
using ECommerceAgents.Shared.Workflows;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;
using System.Text.Json;
using S = ECommerceAgents.Shared.Approvals.ApprovalStatus;

namespace ECommerceAgents.Orchestrator.Approvals;

/// <summary>A refusal that maps one-to-one onto an HTTP status; routes turn it into <c>{"detail": ...}</c>.</summary>
public sealed class ApprovalHttpException(int statusCode, string detail) : Exception(detail)
{
    public int StatusCode { get; } = statusCode;
    public string Detail { get; } = detail;
}

/// <summary>Who is acting, read once from the verified token by the route — never from a request body.</summary>
public sealed record Actor(string Email, string Role);

/// <summary>What <see cref="ApprovalsService.DecideAsync"/> produced: the approval after the decision and what executing it returned.</summary>
public sealed record DecideOutcome(Approval Approval, JsonElement Result)
{
    /// <summary>The body of <c>POST /api/approvals/{id}/decide</c>.</summary>
    public object ToBody() => new Dictionary<string, object?> { ["approval"] = Approval.ToJson(), ["result"] = Result };
}

/// <summary>
/// Deciding approvals — the ONE code path behind the unified API and both legacy endpoints; the .NET twin of Python's
/// <c>orchestrator/approvals_service.py</c>. <c>POST /api/approvals/{id}/decide</c>, <c>POST /api/orchestration/{run_id}/resume</c> and
/// <c>POST /api/admin/hitl/requests/{id}/approve|deny</c> all call <see cref="DecideAsync"/>; none of them touches a claim, a
/// transition or an executor itself.
/// </summary>
/// <remarks>
/// Who may decide (checked before anything is claimed, so a refusal changes nothing):
/// <list type="bullet">
/// <item>only staff (<c>support</c> or <c>admin</c>);</item>
/// <item>never the requester — compared case-insensitively, the same rule the <c>approvals_no_self_approval</c> CHECK enforces;</item>
/// <item>the staff role must be at or above the approval's <c>assigned_role</c>.</item>
/// </list>
/// Exactly-once execution: tool approvals take <see cref="ApprovalRepository.ClaimAsync"/> (a fenced, TTL'd claim on the approvals
/// row), run the action keyed by the approval id, and record the outcome fenced by the same token; workflow approvals resume the
/// paused workflow under the <c>hitl_requests</c> claim fenced by <c>claimed_at</c>, as the requester, with the reviewer recorded
/// separately. A decision that fails to execute is not recorded: the claim is handed back and the reviewer can try again. Log lines
/// carry ids and fixed strings only; anything request-derived goes through <see cref="LogSafe.Clean"/>.
/// </remarks>
public sealed class ApprovalsService(
    DatabasePool pool,
    ApprovalRepository repo,
    ApprovalIntake intake,
    ApprovalPolicy? policy,
    AgentSettings settings,
    IServiceProvider services,
    ILogger<ApprovalsService> logger)
{
    public const int MaxNoteLength = 2000;
    private const string WorkflowKind = "return_approval";

    public ApprovalRepository Repository => repo;

    // ───────────────────────────── authorisation ─────────────────────────────

    private static bool SamePerson(string? a, string? b) =>
        !string.IsNullOrEmpty(a) && !string.IsNullOrEmpty(b)
        && string.Equals(a.Trim(), b.Trim(), StringComparison.OrdinalIgnoreCase);

    /// <summary>Whether <paramref name="actor"/> may see/act on this approval's queue: staff, at or above the assigned role.</summary>
    public static bool CanReview(Approval approval, Actor actor) =>
        ApprovalIntake.Rank(actor.Role) >= ApprovalIntake.Rank(approval.AssignedRole ?? "admin")
        && ApprovalIntake.Rank(approval.AssignedRole ?? "admin") > 0;

    /// <summary>
    /// A merchant proposal is decided by its seller on <c>/api/seller/proposals/{id}/decide</c> (Python only), never here. Approve and reject
    /// have no executor on this path, and <c>request_info</c> would park the batch in <c>needs_info</c> where nobody can answer it (its
    /// requester is the system actor). The answer is the one a missing id gets (404, same body), on every staff route, the same as Python.
    /// </summary>
    public static void RefuseProposal(Approval approval)
    {
        if (approval.Source == "proposal")
            throw new ApprovalHttpException(404, "Approval not found");
    }

    /// <summary>
    /// Sources whose <em>subject</em> is the intended reviewer, so the beneficiary rule in <see cref="AuthorizeDecider"/> does not apply: a
    /// merchant proposal is the seller's own and the seller decides it (Python's seller routes), never through this function. Every other
    /// source is decided by staff, and the reviewer may never be the party who receives the money or benefit of the action. Same constant
    /// as Python's <c>SELLER_DECIDED_SOURCES</c>.
    /// </summary>
    public static readonly IReadOnlySet<string> SellerDecidedSources = new HashSet<string> { "proposal" };

    /// <summary>
    /// Throws 403 unless <paramref name="actor"/> may decide <paramref name="approval"/>. Pure: reads nothing, changes nothing. Two "not you"
    /// rules: never the requester, and (for a staff-decided source) never the subject, the customer the action is for: an autonomous
    /// action's requester is the system actor, so without the second rule a staff member could approve a credit to their own account.
    /// </summary>
    public static void AuthorizeDecider(Approval approval, Actor actor)
    {
        if (ApprovalIntake.Rank(actor.Role) == 0) throw new ApprovalHttpException(403, "Staff access required");
        if (SamePerson(actor.Email, approval.RequesterEmail))
            throw new ApprovalHttpException(403, "You cannot approve your own request");
        if (!SellerDecidedSources.Contains(approval.Source) && SamePerson(actor.Email, approval.SubjectEmail))
            throw new ApprovalHttpException(403, "You cannot decide a request that is for you");
        if (!CanReview(approval, actor)) throw new ApprovalHttpException(403, "This approval is routed to a manager");
    }

    // ───────────────────────────── reads ─────────────────────────────

    public async Task<Approval> LoadAsync(string approvalId)
    {
        if (!Guid.TryParse(approvalId, out var id)) throw new ApprovalHttpException(404, "Approval not found");
        return await repo.GetAsync(id) ?? throw new ApprovalHttpException(404, "Approval not found");
    }

    /// <summary>The reviewer's view: the approval, its audit events, the policy evidence and what it would execute.</summary>
    public async Task<Dictionary<string, object?>> DetailAsync(Approval approval)
    {
        var body = approval.ToJson();
        body["events"] = (await repo.ListEventsAsync(approval.Id)).Select(e => e.ToJson()).ToList();
        string[] editable;
        try
        {
            editable = policy?.Kind(approval.Kind).EditableFields.ToArray() ?? [];
        }
        catch (PolicyException)
        {
            editable = [];
        }
        body["editable_fields"] = editable;
        body["source_detail"] = await SourceDetailAsync(approval);
        return body;
    }

    private async Task<Dictionary<string, object?>?> SourceDetailAsync(Approval approval)
    {
        if (approval.SourceRef is not { } reference) return null;
        await using var conn = await pool.OpenAsync();
        if (approval.Source == "tool")
        {
            var row = await conn.QueryFirstOrDefaultAsync(
                "SELECT tool_name, tool_input, status, agent_name FROM tool_approval_requests WHERE id = @reference", new { reference });
            if (row is not null)
            {
                return new Dictionary<string, object?>
                {
                    ["tool_name"] = (string)row.tool_name,
                    ["agent_name"] = (string?)row.agent_name,
                    ["status"] = (string)row.status,
                    ["tool_input"] = ParseJson((object?)row.tool_input) ?? EmptyObject(),
                };
            }
        }
        if (approval.Source == "workflow")
        {
            var row = await conn.QueryFirstOrDefaultAsync(
                "SELECT workflow_run_id, status, payload FROM hitl_requests WHERE id = @reference", new { reference });
            if (row is not null)
            {
                return new Dictionary<string, object?>
                {
                    ["run_id"] = ((Guid)row.workflow_run_id).ToString(),
                    ["status"] = (string)row.status,
                    ["payload"] = ParseJson((object?)row.payload) ?? EmptyObject(),
                };
            }
        }
        return null;
    }

    private static JsonElement? ParseJson(object? raw)
    {
        if (raw is not string text || string.IsNullOrWhiteSpace(text)) return null;
        using var doc = JsonDocument.Parse(text);
        return doc.RootElement.Clone();
    }

    private static JsonElement EmptyObject() => JsonSerializer.SerializeToElement(new Dictionary<string, object?>());

    // ───────────────────────────── decide ─────────────────────────────

    /// <summary>Applies a staff decision (<c>approve</c> | <c>reject</c> | <c>request_info</c>) to an approval.</summary>
    public async Task<DecideOutcome> DecideAsync(
        string approvalId,
        Actor actor,
        string decision,
        IReadOnlyDictionary<string, JsonElement>? edits = null,
        string? note = null)
    {
        var approval = await LoadAsync(approvalId);
        RefuseProposal(approval); // before anything else: not even a 403 hints at a decision path that does not exist
        AuthorizeDecider(approval, actor);
        var trimmed = (note ?? "").Trim();
        var cleanNote = trimmed.Length == 0 ? null : trimmed[..Math.Min(trimmed.Length, MaxNoteLength)];
        var edited = edits ?? new Dictionary<string, JsonElement>();

        switch (decision)
        {
            case "request_info":
                if (edited.Count > 0) throw new ApprovalHttpException(422, "Edits cannot accompany a request for information");
                if (cleanNote is null) throw new ApprovalHttpException(422, "Say what you need to know in the note");
                return await RequestInfoAsync(approval, actor, cleanNote);
            case "reject":
                if (edited.Count > 0) throw new ApprovalHttpException(422, "A rejection takes no edits");
                return await RejectAsync(approval, actor, cleanNote);
            case "approve":
                return await ApproveAsync(approval, actor, edited, cleanNote);
            default:
                throw new ApprovalHttpException(422, "decision must be approve, reject or request_info");
        }
    }

    private static void Require(Approval approval, params string[] allowed)
    {
        if (allowed.Contains(approval.Status)) return;
        if (approval.Status == S.NeedsInfo) throw new ApprovalHttpException(409, "Waiting for the requester's answer");
        throw new ApprovalHttpException(409, $"Request is already {approval.Status}");
    }

    /// <summary>Takes the approval's execution claim, or throws 409. Returns the claim token.</summary>
    private async Task<string> ClaimAsync(Approval approval, params string[] statuses)
    {
        var token = Guid.NewGuid().ToString("N");
        if (await repo.ClaimAsync(approval.Id, token, statuses, settings.HitlClaimTtlSeconds) is not null) return token;
        var current = await repo.GetAsync(approval.Id);
        if (current is not null && statuses.Contains(current.Status))
            throw new ApprovalHttpException(409, "Request is being decided by someone else");
        throw new ApprovalHttpException(409, $"Request is already {current?.Status ?? "gone"}");
    }

    /// <summary>
    /// Takes the execution claim, then re-runs the decider check against the row as it is NOW. <see cref="DecideAsync"/> authorised
    /// against an earlier read; an SLA escalation (support to manager) can land between that read and the claim, and a reviewer must not
    /// finish what is now above their role. The claim closes the window (<c>EscalateAsync</c> refuses while one is held), so once it is
    /// ours <c>assigned_role</c> cannot change under us. A failed fresh check hands the claim back and throws the normal 403 with nothing
    /// executed. Returns the claim token and the fresh row.
    /// </summary>
    private async Task<(string Token, Approval Fresh)> ClaimAndReauthorizeAsync(Approval approval, Actor actor, params string[] statuses)
    {
        var token = await ClaimAsync(approval, statuses);
        try
        {
            var fresh = await LoadAsync(approval.Id.ToString());
            AuthorizeDecider(fresh, actor);
            return (token, fresh);
        }
        catch
        {
            await ReleaseAsync(approval.Id, token);
            throw;
        }
    }

    // Not cancellable: a request that was cancelled (client disconnect, shutdown) must still hand its claim back.
    private Task ReleaseAsync(Guid approvalId, string token) => repo.ReleaseClaimAsync(approvalId, token);

    private static async Task PostMessageAsync(Tx tx, Guid? conversationId, string role, string text)
    {
        if (conversationId is null) return;
        await tx.Conn.ExecuteAsync(
            "INSERT INTO messages (conversation_id, role, content, agent_name) VALUES (@conversationId, @role, @text, 'approvals')",
            new { conversationId, role, text }, tx.Trans);
        await tx.Conn.ExecuteAsync(
            "UPDATE conversations SET last_message_at = NOW() WHERE id = @conversationId", new { conversationId }, tx.Trans);
    }

    private static JsonElement ResultOf(string status) =>
        JsonSerializer.SerializeToElement(new Dictionary<string, object?> { ["status"] = status });

    // ── request more information ─────────────────────────────────────────────

    /// <summary>
    /// The approval moves to <c>needs_info</c> and the question lands in the requester's conversation. Nothing MAF-side happens: a
    /// paused workflow stays paused, and its <c>hitl_requests</c> row is parked as <c>needs_info</c> so a resume cannot be claimed while
    /// the customer is being asked.
    /// </summary>
    private async Task<DecideOutcome> RequestInfoAsync(Approval approval, Actor actor, string note)
    {
        Require(approval, S.PendingReview);
        var (token, fresh) = await ClaimAndReauthorizeAsync(approval, actor, S.PendingReview);
        approval = fresh;
        var parked = false;
        try
        {
            if (await ExecutedResultAsync(approval) is not null)
            {
                // The action already ran (an approve lost its claim before recording): asking the requester about it now would park a
                // finished action. Only recording it (approve) makes sense.
                throw new ApprovalHttpException(409, "The action already ran; approve to record it");
            }
            if (approval.Source == "workflow" && approval.SourceRef is { } hitlId)
            {
                await using var conn = await pool.OpenAsync();
                parked = await conn.ExecuteScalarAsync<Guid?>(
                    "UPDATE hitl_requests SET status = 'needs_info' WHERE id = @hitlId AND status = 'pending' RETURNING id",
                    new { hitlId }) is not null;
                if (!parked) throw new ApprovalHttpException(409, "Request is being processed");
            }

            var updated = await repo.InTransactionAsync(async tx =>
            {
                await PostMessageAsync(
                    tx,
                    approval.ConversationId,
                    "assistant",
                    $"We need a bit more information about your {approval.Kind.Replace('_', ' ')} request: {note}");
                return await repo.TransitionAsync(
                    approval.Id,
                    [S.PendingReview],
                    S.NeedsInfo,
                    new TransitionOptions
                    {
                        ActorEmail = actor.Email,
                        ActorRole = actor.Role,
                        Action = "info_requested",
                        Payload = new Dictionary<string, object?> { ["note"] = note },
                        ClaimedBy = token,
                    },
                    tx);
            });
            return new DecideOutcome(updated, ResultOf(S.NeedsInfo));
        }
        catch
        {
            if (parked) await UnparkWorkflowAsync(approval);
            await ReleaseAsync(approval.Id, token);
            throw;
        }
    }

    private async Task UnparkWorkflowAsync(Approval approval)
    {
        if (approval.SourceRef is not { } hitlId) return;
        await using var conn = await pool.OpenAsync();
        await conn.ExecuteAsync(
            "UPDATE hitl_requests SET status = 'pending' WHERE id = @hitlId AND status = 'needs_info'", new { hitlId });
    }

    /// <summary>The requester's answer: <c>needs_info</c> to <c>pending_review</c>. Only the requester may answer.</summary>
    public async Task<DecideOutcome> ProvideInfoAsync(string approvalId, Actor actor, string message)
    {
        var approval = await LoadAsync(approvalId);
        RefuseProposal(approval);
        if (!SamePerson(actor.Email, approval.RequesterEmail)) throw new ApprovalHttpException(403, "Only the requester can answer");
        var trimmed = (message ?? "").Trim();
        if (trimmed.Length == 0) throw new ApprovalHttpException(422, "message must not be empty");
        trimmed = trimmed[..Math.Min(trimmed.Length, MaxNoteLength)];
        if (approval.Status != S.NeedsInfo)
            throw new ApprovalHttpException(409, $"Request is {approval.Status}, not waiting for information");

        try
        {
            var updated = await repo.InTransactionAsync(async tx =>
            {
                if (approval.Source == "workflow" && approval.SourceRef is { } hitlId)
                {
                    await tx.Conn.ExecuteAsync(
                        "UPDATE hitl_requests SET status = 'pending' WHERE id = @hitlId AND status = 'needs_info'",
                        new { hitlId }, tx.Trans);
                }
                var moved = await repo.TransitionAsync(
                    approval.Id,
                    [S.NeedsInfo],
                    S.PendingReview,
                    new TransitionOptions
                    {
                        ActorEmail = actor.Email,
                        ActorRole = actor.Role,
                        Action = "info_provided",
                        Payload = new Dictionary<string, object?> { ["message"] = trimmed },
                    },
                    tx);
                await PostMessageAsync(tx, approval.ConversationId, "user", trimmed);
                return moved;
            });
            return new DecideOutcome(updated, ResultOf(S.PendingReview));
        }
        catch (InvalidTransitionException)
        {
            throw new ApprovalHttpException(409, "Request is no longer waiting for information");
        }
    }

    // ── reject ───────────────────────────────────────────────────────────────

    private async Task<DecideOutcome> RejectAsync(Approval approval, Actor actor, string? note)
    {
        Require(approval, S.PendingReview, S.NeedsInfo);
        return approval.Source switch
        {
            "workflow" => await FinishWorkflowAsync(approval, actor, approve: false, new Dictionary<string, JsonElement>(), note),
            "tool" => await RejectToolAsync(approval, actor, note),
            _ => throw new ApprovalHttpException(422, $"Approvals from source '{approval.Source}' cannot be decided yet"),
        };
    }

    private async Task<DecideOutcome> RejectToolAsync(Approval approval, Actor actor, string? note)
    {
        var (token, fresh) = await ClaimAndReauthorizeAsync(approval, actor, S.PendingReview, S.NeedsInfo);
        approval = fresh;
        try
        {
            var resolved = await ResolveToolRequestAsync(approval.SourceRef, "denied", actor.Email, note, null);
            if (!resolved) throw new ApprovalHttpException(409, "Request was already resolved by another admin");
            var updated = await RecordAsync(approval, actor, token, S.Rejected, "reject", note, new Dictionary<string, JsonElement>());
            logger.LogInformation("approval.rejected id={Id} source=tool", approval.Id);
            return new DecideOutcome(updated, ResultOf("denied"));
        }
        catch
        {
            await ReleaseAsync(approval.Id, token);
            throw;
        }
    }

    /// <summary>Records the decision on the approval, fenced by the claim token; a lost claim is a 409.</summary>
    private async Task<Approval> RecordAsync(
        Approval approval, Actor actor, string token, string to, string decision, string? note,
        IReadOnlyDictionary<string, JsonElement> edits)
    {
        try
        {
            var payload = new Dictionary<string, object?> { ["decision"] = decision, ["note"] = note, ["edits"] = edits };
            return await repo.TransitionAsync(
                approval.Id,
                to == S.Rejected ? [S.PendingReview, S.NeedsInfo] : [S.PendingReview],
                to,
                new TransitionOptions
                {
                    ActorEmail = actor.Email,
                    ActorRole = actor.Role,
                    Action = "decided",
                    Payload = payload,
                    ClaimedBy = token,
                    DecidedBy = actor.Email,
                    Decision = payload,
                });
        }
        catch (ClaimLostException)
        {
            throw new ApprovalHttpException(409, "This approval was taken over by another reviewer");
        }
        catch (InvalidTransitionException)
        {
            throw new ApprovalHttpException(409, "Request is no longer pending review");
        }
    }

    // ── approve ──────────────────────────────────────────────────────────────

    private async Task<DecideOutcome> ApproveAsync(
        Approval approval, Actor actor, IReadOnlyDictionary<string, JsonElement> edits, string? note)
    {
        Require(approval, S.PendingReview);
        return approval.Source switch
        {
            "workflow" => await FinishWorkflowAsync(approval, actor, approve: true, edits, note),
            "tool" => await ApproveToolAsync(approval, actor, edits, note),
            _ => throw new ApprovalHttpException(422, $"Approvals from source '{approval.Source}' cannot be decided yet"),
        };
    }

    private static readonly HashSet<string> RefundMethods = ["original_payment", "store_credit"];

    /// <summary>
    /// The tool input with a reviewer's <paramref name="edits"/> applied — ONLY for fields on the policy's <c>editable_fields</c>. Anything
    /// else is a 422 and nothing is merged: an allowlist, so a reviewer cannot rewrite <c>order_id</c> or <c>return_id</c> and point an
    /// approval at someone else's order.
    /// </summary>
    internal JsonElement MergeEdits(string toolName, JsonElement toolInput, IReadOnlyDictionary<string, JsonElement> edits)
    {
        if (edits.Count == 0) return toolInput;
        HashSet<string> allowed;
        try
        {
            allowed = policy?.Kind(toolName).EditableFields.ToHashSet() ?? [];
        }
        catch (PolicyException)
        {
            allowed = [];
        }
        var rejected = edits.Keys.Where(k => !allowed.Contains(k)).Order(StringComparer.Ordinal).ToList();
        if (rejected.Count > 0)
            throw new ApprovalHttpException(422, $"not editable for {toolName}: {string.Join(", ", rejected)}");
        if (edits.TryGetValue("refund_method", out var method)
            && !(method.ValueKind == JsonValueKind.String && RefundMethods.Contains(method.GetString()!)))
            throw new ApprovalHttpException(422, "refund_method must be 'original_payment' or 'store_credit'");

        var merged = new Dictionary<string, JsonElement>();
        if (toolInput.ValueKind == JsonValueKind.Object)
        {
            foreach (var property in toolInput.EnumerateObject()) merged[property.Name] = property.Value;
        }
        foreach (var (key, value) in edits) merged[key] = value;
        return JsonSerializer.SerializeToElement(merged);
    }

    private async Task<DecideOutcome> ApproveToolAsync(
        Approval approval, Actor actor, IReadOnlyDictionary<string, JsonElement> edits, string? note)
    {
        var reference = approval.SourceRef;
        await using var conn = await pool.OpenAsync();
        var row = await conn.QueryFirstOrDefaultAsync(
            "SELECT user_email, tool_name, tool_input FROM tool_approval_requests WHERE id = @reference", new { reference });
        if (row is null) throw new ApprovalHttpException(409, "The request behind this approval no longer exists");
        string userEmail = row.user_email;
        string toolName = ApprovalIntake.CanonicalToolName((string)row.tool_name);
        // A request stored before the gate wrote snake_case (PascalCase tool name, camelCase input keys) is mapped the same way the name is;
        // when two keys collapse onto one name the mapping is a guess, so the row is refused up front instead of half executed.
        var canonical = ApprovalIntake.CanonicalToolInput(ParseJson((object?)row.tool_input) ?? EmptyObject());
        if (canonical.AmbiguousKeys is { Count: > 0 } ambiguous)
        {
            await FlagLegacyInputAsync(approval, actor, ambiguous);
            throw new ApprovalHttpException(
                422, "This request was stored in an older format whose input cannot be mapped unambiguously; reject it and ask the customer to resubmit");
        }
        var merged = MergeEdits(toolName, canonical.Input, edits); // the allowlist, before anything is claimed

        var (token, fresh) = await ClaimAndReauthorizeAsync(approval, actor, S.PendingReview);
        approval = fresh;
        JsonElement result;
        Approval approved;
        try
        {
            result = await HitlActionExecutor.ExecuteApprovedAsync(pool, toolName, merged, userEmail, approval.Id);
            var resolved = await ResolveToolRequestAsync(reference, "approved", actor.Email, note, result);
            // Not resolved but executed FOR THIS APPROVAL: an earlier attempt ran the action and then lost its claim before recording.
            // ExecuteApprovedAsync just replayed that run's result (it is keyed by the approval id), so this attempt only records it.
            if (!resolved && await ExecutedResultAsync(approval) is null)
                throw new ApprovalHttpException(409, "Request was already resolved by another admin");
            approved = await RecordAsync(
                approval, actor, token, edits.Count > 0 ? S.ApprovedWithEdits : S.Approved, "approve", note, edits);
        }
        catch
        {
            // Hand the claim back so the reviewer can retry: the executor is keyed by this approval's id, so a retry replays a
            // completed result instead of acting twice.
            await ReleaseAsync(approval.Id, token);
            throw;
        }

        var success = result.ValueKind == JsonValueKind.Object
            && result.TryGetProperty("success", out var s) && s.ValueKind == JsonValueKind.True;
        var final = await repo.TransitionAsync(
            approval.Id,
            [approved.Status],
            success ? S.Executed : S.Failed,
            new TransitionOptions
            {
                ActorEmail = actor.Email,
                ActorRole = actor.Role,
                Action = success ? "executed" : "execution_failed",
                Payload = new Dictionary<string, object?> { ["success"] = success },
            });
        logger.LogInformation("approval.approved id={Id} source=tool success={Success}", approval.Id, success);
        return new DecideOutcome(final, result);
    }

    /// <summary>
    /// The stored result of a tool request that already ran FOR THIS APPROVAL, else null. An approve that executed the action and then lost
    /// its claim before recording leaves the request <c>executed</c> (carrying <c>approval_id</c> and the result) and the approval still
    /// <c>pending_review</c>: a completed action, not a conflict. A retry finishes recording it, and the SLA timer (Python's worker,
    /// <c>expire()</c>) does the same instead of expiring something that happened.
    /// </summary>
    private async Task<JsonElement?> ExecutedResultAsync(Approval approval)
    {
        if (approval.Source != "tool" || approval.SourceRef is not { } reference) return null;
        await using var conn = await pool.OpenAsync();
        var row = await conn.QueryFirstOrDefaultAsync(
            "SELECT status, approval_id, execution_result FROM tool_approval_requests WHERE id = @reference", new { reference });
        if (row is null || (string)row.status != "executed" || row.approval_id is not Guid linked || linked != approval.Id) return null;
        return ParseJson((object?)row.execution_result) ?? EmptyObject();
    }

    /// <summary>Marks an approval whose stored input could not be mapped, once: the row stays undecided and the reason is in its audit trail.</summary>
    private async Task FlagLegacyInputAsync(Approval approval, Actor actor, IReadOnlyCollection<string> keys)
    {
        var events = await repo.ListEventsAsync(approval.Id);
        if (events.Any(e => e.Action == "legacy_input_refused")) return;
        await repo.AppendEventAsync(
            approval.Id, "legacy_input_refused", actor.Email, actor.Role,
            new Dictionary<string, object?> { ["ambiguous_keys"] = keys.Order(StringComparer.Ordinal).ToList() });
        logger.LogWarning("approval.legacy_input_refused id={Id}", approval.Id);
    }

    /// <summary>
    /// Closes out a <c>tool_approval_requests</c> row (Python's <c>resolve_hitl_request</c>): accepts one still <c>pending</c> or
    /// <c>processing</c>; an approval with an execution result ends as <c>executed</c>. Returns whether a row was updated.
    /// </summary>
    private async Task<bool> ResolveToolRequestAsync(
        Guid? requestId, string decision, string adminEmail, string? note, JsonElement? executionResult)
    {
        var finalStatus = decision == "approved" && executionResult is not null ? "executed" : decision;
        await using var conn = await pool.OpenAsync();
        var affected = await conn.ExecuteAsync(
            @"UPDATE tool_approval_requests
                 SET status = @finalStatus, admin_note = @note, approved_by = @adminEmail,
                     execution_result = @result::jsonb, resolved_at = NOW()
               WHERE id = @requestId AND status IN ('pending', 'processing')",
            new
            {
                finalStatus,
                note,
                adminEmail,
                result = executionResult?.GetRawText(),
                requestId,
            });
        return affected == 1;
    }

    // ── workflow approvals ───────────────────────────────────────────────────

    /// <summary>Validates a reviewer's edits against the policy allowlist and builds the workflow's <see cref="ReturnDecision"/>.</summary>
    private ReturnDecision BuildWorkflowDecision(
        Approval approval, Actor actor, bool approve, IReadOnlyDictionary<string, JsonElement> edits, string? note)
    {
        HashSet<string> allowed;
        try
        {
            allowed = policy?.Kind(WorkflowKind).EditableFields.ToHashSet() ?? [];
        }
        catch (PolicyException)
        {
            allowed = [];
        }
        var rejected = edits.Keys.Where(k => !allowed.Contains(k)).Order(StringComparer.Ordinal).ToList();
        if (rejected.Count > 0) throw new ApprovalHttpException(422, $"Not editable: {string.Join(", ", rejected)}");

        decimal? amount = null;
        if (edits.TryGetValue("amount", out var rawAmount) && rawAmount.ValueKind != JsonValueKind.Null)
        {
            if (rawAmount.ValueKind != JsonValueKind.Number || !rawAmount.TryGetDecimal(out var value) || value < 0)
                throw new ApprovalHttpException(422, "amount must be a non-negative number");
            if (approval.Amount is { } ceiling && value > ceiling)
                throw new ApprovalHttpException(422, "amount cannot exceed what is under review");
            amount = value;
        }

        var resolution = "store_credit";
        if (edits.TryGetValue("resolution", out var rawResolution))
        {
            resolution = rawResolution.ValueKind == JsonValueKind.String ? rawResolution.GetString()! : "";
            if (!ReturnDecision.Resolutions.Contains(resolution))
                throw new ApprovalHttpException(422, $"resolution must be one of: {string.Join(", ", ReturnDecision.Resolutions)}");
        }

        return new ReturnDecision(
            approve ? "approve" : "reject", resolution, amount, note ?? "", actor.Email, approval.Id.ToString());
    }

    private async Task<DecideOutcome> FinishWorkflowAsync(
        Approval approval, Actor actor, bool approve, IReadOnlyDictionary<string, JsonElement> edits, string? note)
    {
        var returnDecision = BuildWorkflowDecision(approval, actor, approve, edits, note);
        if (approval.SourceRef is not { } hitlId)
            throw new ApprovalHttpException(409, "The paused run behind this approval no longer exists");

        // Take the approval's claim, then check the reviewer's role again against the row as it is now. The check in DecideAsync ran on
        // an earlier read: an SLA escalation (support to manager) can land between that read and the resume, and a support reviewer must
        // not finish what is now a manager's decision. The claim closes the window — EscalateAsync refuses while one is held.
        var statuses = approve ? new[] { S.PendingReview } : [S.PendingReview, S.NeedsInfo];
        var token = await ClaimAsync(approval, statuses);
        Approval decided;
        ModeRunResult payload;
        try
        {
            var fresh = await LoadAsync(approval.Id.ToString());
            AuthorizeDecider(fresh, actor);
            if (fresh.Status == S.NeedsInfo)
            {
                // A reviewer may reject while the customer is being asked; resume needs 'pending'.
                await UnparkWorkflowAsync(fresh);
            }
            payload = await ResumeWorkflowAsync(hitlId, returnDecision, actor.Email);

            var to = !approve ? S.Rejected : edits.Count > 0 ? S.ApprovedWithEdits : S.Approved;
            try
            {
                var detail = new Dictionary<string, object?>
                {
                    ["decision"] = returnDecision.Decision, ["note"] = note, ["edits"] = edits,
                };
                decided = await repo.TransitionAsync(
                    approval.Id,
                    approve ? [S.PendingReview] : [S.PendingReview, S.NeedsInfo],
                    to,
                    new TransitionOptions
                    {
                        ActorEmail = actor.Email,
                        ActorRole = actor.Role,
                        Action = "decided",
                        Payload = detail,
                        ClaimedBy = token,
                        DecidedBy = actor.Email,
                        Decision = detail,
                    });
            }
            catch (ClaimLostException)
            {
                // The workflow is already resumed and the claim was taken over meanwhile: the new holder owns the decision. A conflict,
                // like every other lost claim, not a server error.
                logger.LogWarning("approval.claim_lost id={Id}", approval.Id);
                throw new ApprovalHttpException(409, "This approval was taken over by another reviewer");
            }
            catch (InvalidTransitionException)
            {
                // The workflow is already resumed; the inbox row moved under us (a concurrent expiry, say).
                logger.LogWarning("approval.decision_not_recorded id={Id}", approval.Id);
                throw new ApprovalHttpException(409, "Request is no longer pending review");
            }
        }
        catch
        {
            await ReleaseAsync(approval.Id, token);
            throw;
        }

        var final = decided;
        if (approve)
        {
            var completed = payload.Completed ?? true;
            final = await repo.TransitionAsync(
                approval.Id,
                [decided.Status],
                completed ? S.Executed : S.Failed,
                new TransitionOptions
                {
                    ActorEmail = actor.Email,
                    ActorRole = actor.Role,
                    Action = completed ? "executed" : "execution_failed",
                    Payload = new Dictionary<string, object?>(),
                });
        }
        logger.LogInformation("approval.decided id={Id} source=workflow approve={Approve}", approval.Id, approve);
        return new DecideOutcome(
            final,
            JsonSerializer.SerializeToElement(new Dictionary<string, object?>
            {
                ["text"] = payload.Text,
                ["agents_involved"] = payload.AgentsInvolved,
            }));
    }

    /// <summary>
    /// Resumes a paused <c>workflow:return-replace</c> run from its checkpoint with a <see cref="ReturnDecision"/>. The claim / fence /
    /// resume machinery the resume route always had, unchanged in behaviour:
    /// claims the <c>hitl_requests</c> row atomically (<c>pending</c> to <c>processing</c>) BEFORE the workflow runs, so two decisions
    /// cannot both resume; a <c>processing</c> row whose <c>claimed_at</c> is older than the TTL is a crashed resume and can be taken over;
    /// <c>claimed_at</c> is the claim's fencing token, so the release and the final write change nothing once the claim was taken over; and
    /// the remaining steps run as the REQUESTER (tools read identity from <see cref="RequestContext"/>), with the reviewer restored
    /// afterwards and recorded separately.
    /// </summary>
    public async Task<ModeRunResult> ResumeWorkflowAsync(Guid hitlId, ReturnDecision decision, string reviewerEmail)
    {
        await using var conn = await pool.OpenAsync();
        var claimed = await conn.QueryFirstOrDefaultAsync(
            // Claimable = pending, or 'processing' with a claim older than the TTL (a resume whose process died before releasing it;
            // claimed_at is NULL for rows claimed before migration 0002, which are equally stale).
            @"UPDATE hitl_requests SET status = 'processing', claimed_at = NOW()
               WHERE id = @hitlId
                 AND (status = 'pending' OR (status = 'processing'
                      AND (claimed_at IS NULL OR claimed_at < NOW() - make_interval(secs => @ttl))))
           RETURNING id, kind, request_id, checkpoint_id, payload, user_email, claimed_at, workflow_run_id",
            new { hitlId, ttl = (double)settings.HitlClaimTtlSeconds });

        if (claimed is null)
        {
            var latest = await conn.ExecuteScalarAsync<string?>("SELECT status FROM hitl_requests WHERE id = @hitlId", new { hitlId });
            if (latest is null) throw new ApprovalHttpException(404, "No pending approval found for this run");
            throw new ApprovalHttpException(409, $"Request is already {latest}");
        }

        var claimId = (Guid)claimed.id;
        // claimed_at is the claim's fencing token: every claim and takeover rewrites it.
        var claimToken = (DateTime)claimed.claimed_at;
        var kind = (string)claimed.kind;
        var requestId = (string?)claimed.request_id;
        var checkpointId = (Guid?)claimed.checkpoint_id;
        var runId = (Guid)claimed.workflow_run_id;

        try
        {
            if (kind != WorkflowKind)
            {
                await ReleaseHitlAsync(conn, claimId, claimToken);
                throw new ApprovalHttpException(400, $"Resume not supported for request kind '{kind}'");
            }
            if (requestId is null || checkpointId is null)
            {
                await ReleaseHitlAsync(conn, claimId, claimToken);
                throw new ApprovalHttpException(409, "This pending request predates checkpoint-based resume");
            }
            var registry = services.GetRequiredService<ModeRegistry>();
            if (registry.Get("workflow:return-replace") is not IResumableMode mode)
            {
                await ReleaseHitlAsync(conn, claimId, claimToken);
                throw new ApprovalHttpException(409, "This backend cannot resume workflow runs");
            }
            string? sessionId = SessionIdFrom((object?)claimed.payload);
            if (sessionId is null)
            {
                await ReleaseHitlAsync(conn, claimId, claimToken);
                throw new ApprovalHttpException(409, "This pending request predates checkpoint-based resume");
            }

            // The rest of the workflow (loyalty discount, finalize) reads the caller's identity from RequestContext, which is currently the
            // reviewer. Run it as the customer who asked for the return so their tier is applied, and put the reviewer back afterwards.
            string requesterEmail = (string)claimed.user_email;
            string requesterRole = await conn.ExecuteScalarAsync<string?>(
                "SELECT role FROM users WHERE email = @requesterEmail", new { requesterEmail }) ?? "customer";
            var reviewerContextEmail = RequestContext.CurrentUserEmail;
            var reviewerContextRole = RequestContext.CurrentUserRole;
            ModeRunResult result;
            RequestContext.CurrentUserEmail = requesterEmail;
            RequestContext.CurrentUserRole = requesterRole;
            // Hard stop well inside the claim TTL (the loader validates it is shorter), so a frozen resume ends, and releases, before
            // anyone else may take the row over.
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(settings.HitlResumeTimeoutSeconds));
            try
            {
                result = await mode.ResumeAsync(sessionId, checkpointId.Value.ToString(), requestId, decision, timeout.Token);
            }
            catch (OperationCanceledException) when (timeout.IsCancellationRequested)
            {
                logger.LogWarning("hitl.resume_timeout hitl_id={ClaimId}", claimId);
                await ReleaseHitlAsync(conn, claimId, claimToken);
                throw new ApprovalHttpException(504, "Resume timed out; the approval is still pending.");
            }
            finally
            {
                RequestContext.CurrentUserEmail = reviewerContextEmail;
                RequestContext.CurrentUserRole = reviewerContextRole;
            }

            var approved = decision.IsApproval;
            var decided = await conn.ExecuteAsync(
                @"UPDATE hitl_requests
                     SET status = @status, responded_at = NOW(), response = @response::jsonb
                   WHERE id = @claimId AND status = 'processing' AND claimed_at = @claimToken",
                new
                {
                    claimId,
                    claimToken,
                    status = approved ? "approved" : "rejected",
                    // No responder column exists, so the reviewer is recorded in the response.
                    response = JsonSerializer.Serialize(new { approved, decided_by = reviewerEmail }),
                });
            if (decided != 1)
            {
                // The claim was taken over while this resume ran: someone else owns the decision now. Record nothing.
                logger.LogWarning("hitl.claim_lost hitl_id={ClaimId}", claimId);
                throw new ApprovalHttpException(409, "This approval was taken over by another reviewer");
            }

            // Back-link whatever the resumed run checkpointed, so /runs keeps showing the full trail rather than stopping at the pause.
            if (result.LatestCheckpointId is { } newCheckpoint && Guid.TryParse(newCheckpoint, out var newCid))
            {
                await conn.ExecuteAsync(
                    "UPDATE workflow_checkpoints SET usage_log_id = @runId WHERE checkpoint_id = @newCid", new { runId, newCid });
            }
            return result;
        }
        catch (ApprovalHttpException)
        {
            throw;
        }
        catch (Exception ex)
        {
            // Any other way out without a completed resume (a failure, a cancelled request) hands the claim back so the row is not
            // stuck in 'processing'.
            logger.LogError(ex, "hitl.resume_failed hitl_id={ClaimId}", claimId);
            await ReleaseHitlAsync(conn, claimId, claimToken);
            if (ex is OperationCanceledException) throw;
            throw new ApprovalHttpException(500, "Resume failed; the approval is still pending.");
        }
    }

    /// <summary>Fenced by the claim token: a claim that was taken over is not the caller's to release.</summary>
    private static Task<int> ReleaseHitlAsync(System.Data.Common.DbConnection conn, Guid claimId, DateTime claimToken) =>
        conn.ExecuteAsync(
            @"UPDATE hitl_requests SET status = 'pending', claimed_at = NULL
               WHERE id = @claimId AND status = 'processing' AND claimed_at = @claimToken",
            new { claimId, claimToken });

    private static string? SessionIdFrom(object? payload)
    {
        if (payload is not string raw || string.IsNullOrWhiteSpace(raw)) return null;
        try
        {
            return JsonDocument.Parse(raw).RootElement.TryGetProperty("session_id", out var v) ? v.GetString() : null;
        }
        catch (JsonException)
        {
            return null;
        }
    }

    // ───────────────────────────── legacy rows ─────────────────────────────

    /// <summary>The inbox approval for a <c>tool_approval_requests</c> row, creating it for rows that predate the inbox.</summary>
    public async Task<Guid?> EnsureForToolRequestAsync(Guid requestId)
    {
        await using var conn = await pool.OpenAsync();
        var row = await conn.QueryFirstOrDefaultAsync(
            "SELECT user_email, session_id, tool_name, tool_input, approval_id FROM tool_approval_requests WHERE id = @requestId",
            new { requestId });
        if (row is null) return null;
        if (row.approval_id is Guid existing) return existing;
        var approval = await intake.OpenToolApprovalAsync(
            requestId,
            (string)row.user_email,
            row.session_id is Guid sid ? sid.ToString() : null,
            (string)row.tool_name,
            ParseJson((object?)row.tool_input) ?? EmptyObject());
        return approval.Id;
    }

    /// <summary>The inbox approval for a <c>hitl_requests</c> row, creating it for rows that predate the inbox.</summary>
    public async Task<Guid?> EnsureForHitlRequestAsync(Guid hitlId)
    {
        await using var conn = await pool.OpenAsync();
        var row = await conn.QueryFirstOrDefaultAsync(
            @"SELECT h.user_email, h.approval_id, h.payload, u.session_id
                FROM hitl_requests h LEFT JOIN usage_logs u ON u.id = h.workflow_run_id
               WHERE h.id = @hitlId",
            new { hitlId });
        if (row is null) return null;
        if (row.approval_id is Guid existing) return existing;

        // Rebuild the policy decision from the facts the paused run recorded. Rows that predate the facts (or a failed rebuild) get the
        // fail-closed fallback, which routes to a manager: this repair path must never hand a return to a lower tier than the policy
        // would have chosen.
        var payload = ParseJson((object?)row.payload);
        string? orderId = payload is { ValueKind: JsonValueKind.Object } p && p.TryGetProperty("order_id", out var o)
            && o.ValueKind == JsonValueKind.String ? o.GetString() : null;
        decimal? orderTotal = payload is { ValueKind: JsonValueKind.Object } p2 && p2.TryGetProperty("order_total", out var t)
            && t.ValueKind == JsonValueKind.Number && t.TryGetDecimal(out var total) ? total : null;

        RoutedPolicy routed;
        if (orderId is not null)
        {
            routed = await intake.PolicyForReturnAsync((string)row.user_email, orderId, orderTotal);
        }
        else
        {
            var fallback = ApprovalIntake.FallbackDecision();
            routed = new RoutedPolicy(fallback.Route, fallback.Reasons, new Dictionary<string, object?>(), "", null);
        }
        var approval = await intake.OpenWorkflowApprovalAsync(
            hitlId, (string)row.user_email, row.session_id is null ? null : row.session_id.ToString(), routed);
        return approval.Id;
    }
}
