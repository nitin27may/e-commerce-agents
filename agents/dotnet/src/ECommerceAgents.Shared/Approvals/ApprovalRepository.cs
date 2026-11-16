using Dapper;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Logging;
using ECommerceAgents.Shared.Policy;
using Microsoft.Extensions.Logging;
using Npgsql;
using System.Text.Json;

namespace ECommerceAgents.Shared.Approvals;

/// <summary>A connection and the transaction a caller already has open, to join rather than nest.</summary>
public readonly record struct Tx(NpgsqlConnection Conn, NpgsqlTransaction Trans);

/// <summary>What <see cref="ApprovalRepository.CreateAsync"/> inserts. Mirrors the keyword arguments of Python's <c>repo.create</c>.</summary>
public sealed record NewApproval(
    string Source,
    string Kind,
    string RequesterEmail,
    Guid? SourceRef = null,
    string? SubjectEmail = null,
    Guid? ConversationId = null,
    decimal? Amount = null,
    string? PolicyVersion = null,
    string? Route = null,
    string? AssignedRole = null,
    string Status = ApprovalStatus.PendingConfirm,
    object? Recommendation = null,
    DateTime? DueAt = null,
    string? ActorEmail = null,
    string? ActorRole = null
);

/// <summary>
/// The optional parts of a transition: who acted, what to audit and the columns a transition may set. Only these
/// columns can be set (a fixed list, so a column name is never built from input).
/// </summary>
public sealed record TransitionOptions
{
    public string? ActorEmail { get; init; }
    public string? ActorRole { get; init; }
    public string? Action { get; init; }
    public IReadOnlyDictionary<string, object?>? Payload { get; init; }
    /// <summary>Fences the move to the holder of the execution claim; a successful move clears the claim.</summary>
    public string? ClaimedBy { get; init; }
    public string? DecidedBy { get; init; }
    public object? Decision { get; init; }
    public object? Recommendation { get; init; }
    public string? Route { get; init; }
    public string? AssignedRole { get; init; }
    public DateTime? DueAt { get; init; }
    public int? EscalationLevel { get; init; }
}

/// <summary>
/// Repository for the <c>approvals</c> inbox and its append-only <c>approval_events</c> trail — the .NET twin of
/// Python's <c>shared/approvals/repo.py</c>, over the SAME tables, with the same rules:
/// <list type="bullet">
/// <item><b>Guarded transitions.</b> <see cref="TransitionAsync"/> moves an approval only from a state the caller names, under a
/// row lock; any other state throws <see cref="InvalidTransitionException"/> and changes nothing.</item>
/// <item><b>No self-approval.</b> <c>decided_by</c> may never equal <c>requester_email</c> (case-insensitive):
/// <see cref="SelfApprovalException"/> here, the <c>approvals_no_self_approval</c> CHECK in the database.</item>
/// <item><b>Fenced claims.</b> <see cref="ClaimAsync"/> takes a TTL'd claim carrying a token; a transition made with
/// <see cref="TransitionOptions.ClaimedBy"/> fails with <see cref="ClaimLostException"/> if the token is no longer the stored one.</item>
/// <item><b>SLA timers ride the state change.</b> An approval entering <c>pending_review</c> gets its <c>due_at</c> and two
/// outbox timers in the SAME transaction; entering <c>needs_info</c> gets the breach timer. The Python worker
/// (<c>agents/python/worker/</c>) drains them for both stacks, so the topics, dedupe keys and payload shape here are
/// byte-compatible with what Python writes.</item>
/// </list>
/// Who is acting is passed in by the caller; this class never reads a request context. Log lines carry ids and fixed
/// reasons only, and anything that came from a request goes through <see cref="LogSafe.Clean"/>.
/// </summary>
public sealed class ApprovalRepository
{
    public const string TopicReminderDue = "approval.reminder_due";
    public const string TopicSlaBreached = "approval.sla_breached";

    private static readonly HashSet<string> Sources = ["tool", "workflow", "proposal", "event"];
    private static readonly HashSet<string> Routes = ["auto", "support", "manager"];
    private static readonly DateTime UnixEpoch = new(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc);

    private readonly DatabasePool _pool;
    private readonly AgentSettings _settings;
    private readonly ApprovalPolicy? _policy;
    private readonly ILogger? _logger;

    /// <param name="policy">
    /// Supplies each kind's <c>sla_hours</c>. <c>null</c> (policy file unusable) means no deadline and no timers, with a warning,
    /// exactly as Python treats a kind the policy does not know.
    /// </param>
    public ApprovalRepository(DatabasePool pool, AgentSettings settings, ApprovalPolicy? policy, ILogger? logger = null)
    {
        _pool = pool ?? throw new ArgumentNullException(nameof(pool));
        _settings = settings ?? throw new ArgumentNullException(nameof(settings));
        _policy = policy;
        _logger = logger;
    }

    // ───────────────────────────── transactions ─────────────────────────────

    /// <summary>Runs <paramref name="body"/> in one transaction (committed on return, rolled back on a throw).</summary>
    public async Task<T> InTransactionAsync<T>(Func<Tx, Task<T>> body)
    {
        await using var conn = await _pool.OpenAsync();
        await using var trans = await conn.BeginTransactionAsync();
        var result = await body(new Tx(conn, trans));
        await trans.CommitAsync();
        return result;
    }

    private Task<T> Scoped<T>(Tx? tx, Func<Tx, Task<T>> body) =>
        tx is { } joined ? body(joined) : InTransactionAsync(body);

    // ───────────────────────────── time and timers ─────────────────────────────

    private int? SlaHours(string kind)
    {
        try
        {
            return _policy?.Kind(kind).SlaHours;
        }
        catch (PolicyException)
        {
            _logger?.LogWarning("no sla policy for approval kind={Kind}; no timers scheduled", LogSafe.Clean(kind));
            return null;
        }
    }

    /// <summary>The kind's review deadline counted from <paramref name="now"/>, or null when the policy does not know the kind.</summary>
    public DateTime? Deadline(string kind, DateTime now)
    {
        if (_policy is null)
        {
            _logger?.LogWarning("no approval policy loaded; kind={Kind} gets no deadline", LogSafe.Clean(kind));
            return null;
        }
        var hours = SlaHours(kind);
        return hours is null ? null : Approval.TruncateToMicroseconds(now.AddHours(hours.Value));
    }

    private static long EpochMilliseconds(DateTime instant) =>
        (Approval.AsUtc(instant).Ticks - UnixEpoch.Ticks) / TimeSpan.TicksPerMillisecond;

    /// <summary>
    /// Enqueues the reminder and breach timers for an approval's current review level (its <c>due_at</c>). Each timer carries
    /// the level and deadline it was set for and its dedupe key is built from both, so scheduling twice is a no-op and a timer
    /// left from an earlier level (or an earlier trip through <c>needs_info</c>) recognises itself as stale when it fires.
    /// </summary>
    public async Task ScheduleReviewTimersAsync(Tx tx, Approval approval, DateTime now)
    {
        if (approval.DueAt is not { } due) return;
        var level = approval.EscalationLevel;
        var payload = new Dictionary<string, object?>
        {
            ["approval_id"] = approval.Id.ToString(),
            ["level"] = level,
            ["due_at"] = Approval.Iso(due),
        };
        var reminderAt = due > now
            ? Approval.TruncateToMicroseconds(now + ((due - now) * _settings.ApprovalReminderFraction))
            : now;
        var stamp = EpochMilliseconds(due);
        foreach (var (topic, at) in new[] { (TopicReminderDue, reminderAt), (TopicSlaBreached, due) })
        {
            await Outbox.EnqueueAsync(
                tx, topic, payload, at, $"{topic}:{approval.Id}:{level}:{stamp}", approval.Id);
        }
    }

    /// <summary>
    /// Enqueues only the breach timer for an approval waiting on the requester (<c>needs_info</c>): nobody on staff is asked to
    /// act while the customer owes an answer, so there is no reminder, and the breach applies <c>on_timeout</c> rather than escalating.
    /// </summary>
    public async Task ScheduleInfoTimerAsync(Tx tx, Approval approval)
    {
        if (approval.DueAt is not { } due) return;
        await Outbox.EnqueueAsync(
            tx,
            TopicSlaBreached,
            new Dictionary<string, object?>
            {
                ["approval_id"] = approval.Id.ToString(),
                ["level"] = approval.EscalationLevel,
                ["due_at"] = Approval.Iso(due),
            },
            due,
            $"{TopicSlaBreached}:{approval.Id}:{approval.EscalationLevel}:{EpochMilliseconds(due)}",
            approval.Id);
    }

    // ───────────────────────────── row mapping ─────────────────────────────

    private static JsonElement? Json(object? raw)
    {
        if (raw is not string text || string.IsNullOrWhiteSpace(text)) return null;
        using var doc = JsonDocument.Parse(text);
        return doc.RootElement.Clone();
    }

    private static T? Col<T>(IDictionary<string, object> row, string name)
    {
        if (!row.TryGetValue(name, out var value) || value is null || value is DBNull) return default;
        return (T)value;
    }

    private static DateTime? Utc(IDictionary<string, object> row, string name) =>
        Col<DateTime?>(row, name) is { } v ? Approval.AsUtc(v) : null;

    internal static Approval Map(object dapperRow)
    {
        var row = (IDictionary<string, object>)dapperRow;
        return new Approval(
            Id: Col<Guid>(row, "id"),
            Source: Col<string>(row, "source")!,
            SourceRef: Col<Guid?>(row, "source_ref"),
            Kind: Col<string>(row, "kind")!,
            RequesterEmail: Col<string>(row, "requester_email")!,
            SubjectEmail: Col<string>(row, "subject_email"),
            ConversationId: Col<Guid?>(row, "conversation_id"),
            Amount: Col<decimal?>(row, "amount"),
            PolicyVersion: Col<string>(row, "policy_version"),
            Route: Col<string>(row, "route"),
            AssignedRole: Col<string>(row, "assigned_role"),
            Status: Col<string>(row, "status")!,
            Recommendation: Json(Col<string>(row, "recommendation")),
            Decision: Json(Col<string>(row, "decision")),
            DecidedBy: Col<string>(row, "decided_by"),
            DecidedAt: Utc(row, "decided_at"),
            DueAt: Utc(row, "due_at"),
            EscalationLevel: Col<int>(row, "escalation_level"),
            ClaimedAt: Utc(row, "claimed_at"),
            ClaimedBy: Col<string>(row, "claimed_by"),
            CreatedAt: Utc(row, "created_at") ?? DateTime.UtcNow,
            UpdatedAt: Utc(row, "updated_at") ?? DateTime.UtcNow);
    }

    private static string? Dumps(object? value) => value is null ? null : JsonSerializer.Serialize(value);

    private static bool SamePerson(string? a, string? b) =>
        a is not null && b is not null
        && string.Equals(a.Trim(), b.Trim(), StringComparison.OrdinalIgnoreCase);

    // ───────────────────────────── audit ─────────────────────────────

    /// <summary>Appends one audit row and returns its id. There is no update or delete counterpart, by design.</summary>
    public Task<long> AppendEventAsync(
        Guid approvalId,
        string action,
        string? actorEmail = null,
        string? actorRole = null,
        IReadOnlyDictionary<string, object?>? payload = null,
        Tx? tx = null) =>
        Scoped(tx, t => t.Conn.ExecuteScalarAsync<long>(
            @"INSERT INTO approval_events (approval_id, actor_email, actor_role, action, payload)
              VALUES (@approvalId, @actorEmail, @actorRole, @action, @payload::jsonb) RETURNING id",
            new
            {
                approvalId,
                actorEmail,
                actorRole,
                action,
                payload = JsonSerializer.Serialize(payload ?? new Dictionary<string, object?>()),
            },
            t.Trans));

    /// <summary>The audit trail, oldest first.</summary>
    public async Task<IReadOnlyList<ApprovalEvent>> ListEventsAsync(Guid approvalId)
    {
        await using var conn = await _pool.OpenAsync();
        var rows = await conn.QueryAsync(
            "SELECT id, approval_id, actor_email, actor_role, action, payload, created_at FROM approval_events WHERE approval_id = @approvalId ORDER BY id",
            new { approvalId });
        return rows.Select(r =>
        {
            var d = (IDictionary<string, object>)r;
            return new ApprovalEvent(
                Col<long>(d, "id"),
                Col<Guid>(d, "approval_id"),
                Col<string>(d, "actor_email"),
                Col<string>(d, "actor_role"),
                Col<string>(d, "action")!,
                Json(Col<string>(d, "payload")) ?? default,
                Utc(d, "created_at") ?? DateTime.UtcNow);
        }).ToList();
    }

    // ───────────────────────────── reads ─────────────────────────────

    public async Task<Approval?> GetAsync(Guid id)
    {
        await using var conn = await _pool.OpenAsync();
        var row = await conn.QueryFirstOrDefaultAsync("SELECT * FROM approvals WHERE id = @id", new { id });
        return row is null ? null : Map(row);
    }

    public async Task<Approval?> FindBySourceAsync(string source, Guid sourceRef, Tx? tx = null) =>
        await Scoped(tx, async t =>
        {
            var row = await t.Conn.QueryFirstOrDefaultAsync(
                "SELECT * FROM approvals WHERE source = @source AND source_ref = @sourceRef",
                new { source, sourceRef }, t.Trans);
            return row is null ? (Approval?)null : (Approval)Map(row);
        });

    /// <summary>The reviewer queue, oldest deadline first. <paramref name="assignedRoles"/> limits it to what a role may decide.</summary>
    public async Task<IReadOnlyList<Approval>> ListForStaffAsync(
        IReadOnlyCollection<string>? statuses = null,
        IReadOnlyCollection<string>? assignedRoles = null,
        int limit = 50)
    {
        var where = new List<string>();
        var args = new DynamicParameters();
        if (statuses is { Count: > 0 })
        {
            args.Add("statuses", statuses.ToArray());
            where.Add("status = ANY(@statuses::text[])");
        }
        if (assignedRoles is not null)
        {
            args.Add("roles", assignedRoles.ToArray());
            where.Add("assigned_role = ANY(@roles::text[])");
        }
        args.Add("limit", Math.Clamp(limit, 1, 200));
        var clause = where.Count > 0 ? $"WHERE {string.Join(" AND ", where)}" : "";
        await using var conn = await _pool.OpenAsync();
        var rows = await conn.QueryAsync(
            $"SELECT * FROM approvals {clause} ORDER BY due_at NULLS LAST, created_at DESC LIMIT @limit", args);
        return rows.Select(Map).ToList();
    }

    /// <summary>A requester's own approvals, newest first. Case-insensitive on the email.</summary>
    public async Task<IReadOnlyList<Approval>> ListForRequesterAsync(string requesterEmail, int limit = 50)
    {
        await using var conn = await _pool.OpenAsync();
        var rows = await conn.QueryAsync(
            "SELECT * FROM approvals WHERE lower(requester_email) = lower(@requesterEmail) ORDER BY created_at DESC LIMIT @limit",
            new { requesterEmail, limit = Math.Clamp(limit, 1, 200) });
        return rows.Select(Map).ToList();
    }

    // ───────────────────────────── create ─────────────────────────────

    /// <summary>
    /// Inserts an approval (default <c>pending_confirm</c>) and its <c>created</c> audit event, atomically. Idempotent per
    /// <c>(source, source_ref)</c>: a retried creator for the same source record gets the existing row back (and no second
    /// <c>created</c> event). An approval created in <c>pending_review</c> gets its deadline and timers in the same transaction.
    /// </summary>
    public Task<Approval> CreateAsync(NewApproval a, Tx? tx = null) => Scoped(tx, async t =>
    {
        if (!Sources.Contains(a.Source)) throw new ArgumentException($"unknown approval source: {a.Source}");
        if (a.Route is not null && !Routes.Contains(a.Route)) throw new ArgumentException($"unknown approval route: {a.Route}");
        if (!ApprovalStatus.IsKnown(a.Status)) throw new ArgumentException($"unknown approval status: {a.Status}");

        var now = Approval.TruncateToMicroseconds(DateTime.UtcNow);
        var dueAt = a.DueAt;
        if (a.Status == ApprovalStatus.PendingReview && dueAt is null) dueAt = Deadline(a.Kind, now);

        var row = await t.Conn.QueryFirstOrDefaultAsync(
            @"INSERT INTO approvals (source, source_ref, kind, requester_email, subject_email, conversation_id,
                                     amount, policy_version, route, assigned_role, status, recommendation, due_at)
              VALUES (@source, @sourceRef, @kind, @requesterEmail, @subjectEmail, @conversationId,
                      @amount, @policyVersion, @route, @assignedRole, @status, @recommendation::jsonb, @dueAt)
              ON CONFLICT (source, source_ref) WHERE source_ref IS NOT NULL DO NOTHING
              RETURNING *",
            new
            {
                source = a.Source,
                sourceRef = a.SourceRef,
                kind = a.Kind,
                requesterEmail = a.RequesterEmail,
                subjectEmail = a.SubjectEmail,
                conversationId = a.ConversationId,
                amount = a.Amount,
                policyVersion = a.PolicyVersion,
                route = a.Route,
                assignedRole = a.AssignedRole,
                status = a.Status,
                recommendation = Dumps(a.Recommendation),
                dueAt = dueAt is null ? (DateTime?)null : Approval.AsUtc(dueAt.Value),
            },
            t.Trans);

        if (row is null)
        {
            // Lost the race on (source, source_ref): hand back the winner's row.
            var existing = await t.Conn.QueryFirstAsync(
                "SELECT * FROM approvals WHERE source = @source AND source_ref = @sourceRef",
                new { source = a.Source, sourceRef = a.SourceRef }, t.Trans);
            Approval found = Map(existing);
            return found;
        }

        Approval created = Map(row);
        await AppendEventAsync(
            created.Id, "created", a.ActorEmail, a.ActorRole,
            new Dictionary<string, object?> { ["kind"] = a.Kind, ["source"] = a.Source, ["status"] = a.Status }, t);
        _logger?.LogInformation(
            "approval created id={Id} source={Source} kind={Kind}", created.Id, LogSafe.Clean(a.Source), LogSafe.Clean(a.Kind));
        if (a.Status == ApprovalStatus.PendingReview)
        {
            await ScheduleReviewTimersAsync(t, created, now);
        }
        return created;
    });

    // ───────────────────────────── transition ─────────────────────────────

    /// <summary>
    /// Moves an approval to <paramref name="to"/> iff it is currently in one of <paramref name="from"/>; appends an audit event.
    /// With <see cref="TransitionOptions.ClaimedBy"/> the move is fenced to the holder of the execution claim
    /// (<see cref="ClaimLostException"/> otherwise) and a successful move clears the claim. Entering <c>pending_review</c> or
    /// <c>needs_info</c> starts a fresh clock and schedules the matching timers in the same transaction.
    /// </summary>
    public Task<Approval> TransitionAsync(
        Guid id,
        IReadOnlyCollection<string> from,
        string to,
        TransitionOptions? options = null,
        Tx? tx = null) => Scoped(tx, async t =>
    {
        var o = options ?? new TransitionOptions();
        if (from.Count == 0) throw new ArgumentException("TransitionAsync needs at least one from-status");
        foreach (var source in from)
        {
            if (!ApprovalStatus.AllowedTransitions.TryGetValue(source, out var edges) || !edges.Contains(to))
                throw new ArgumentException($"{source} -> {to} is not an approval lifecycle edge");
        }
        if (o.Route is not null && !Routes.Contains(o.Route)) throw new ArgumentException($"unknown approval route: {o.Route}");

        var now = Approval.TruncateToMicroseconds(DateTime.UtcNow);
        var currentRow = await t.Conn.QueryFirstOrDefaultAsync(
            "SELECT * FROM approvals WHERE id = @id FOR UPDATE", new { id }, t.Trans);
        if (currentRow is null) throw new ApprovalNotFoundException(id.ToString());
        Approval current = Map(currentRow);

        if (!from.Contains(current.Status))
        {
            _logger?.LogInformation(
                "approval transition refused id={Id} from={From} to={To}", id, LogSafe.Clean(current.Status), LogSafe.Clean(to));
            throw new InvalidTransitionException($"approval {id} is {current.Status}, not one of [{string.Join(", ", from.Order())}]");
        }
        if (o.ClaimedBy is not null && current.ClaimedBy != o.ClaimedBy)
        {
            _logger?.LogWarning("approval transition refused, claim lost id={Id}", id);
            throw new ClaimLostException($"approval {id}: the execution claim is no longer held");
        }
        if (SamePerson(o.DecidedBy, current.RequesterEmail))
        {
            _logger?.LogWarning("self-approval refused id={Id}", id);
            throw new SelfApprovalException($"approval {id}: the requester cannot decide their own request");
        }

        var dueAt = o.DueAt;
        if ((to == ApprovalStatus.PendingReview || to == ApprovalStatus.NeedsInfo) && dueAt is null)
        {
            // Entering review starts a fresh clock, also when an answer returns it from needs_info; waiting on the requester has
            // its own deadline so an unanswered question cannot hold a request forever.
            dueAt = Deadline(current.Kind, now);
        }

        var args = new DynamicParameters();
        args.Add("id", id);
        args.Add("status", to);
        var sets = new List<string> { "status = @status", "updated_at = NOW()" };
        void Set(string column, object? value, string cast = "")
        {
            args.Add(column, value);
            sets.Add($"{column} = @{column}{cast}");
        }
        if (o.Route is not null) Set("route", o.Route);
        if (o.AssignedRole is not null) Set("assigned_role", o.AssignedRole);
        if (dueAt is not null) Set("due_at", Approval.AsUtc(dueAt.Value));
        if (o.EscalationLevel is not null) Set("escalation_level", o.EscalationLevel);
        if (o.Recommendation is not null) Set("recommendation", Dumps(o.Recommendation), "::jsonb");
        if (o.Decision is not null) Set("decision", Dumps(o.Decision), "::jsonb");
        if (o.DecidedBy is not null)
        {
            Set("decided_by", o.DecidedBy);
            sets.Add("decided_at = NOW()");
        }
        if (o.ClaimedBy is not null) sets.Add("claimed_at = NULL, claimed_by = NULL");

        var updatedRow = await t.Conn.QueryFirstAsync(
            $"UPDATE approvals SET {string.Join(", ", sets)} WHERE id = @id RETURNING *", args, t.Trans);
        Approval updated = Map(updatedRow);

        var eventPayload = new Dictionary<string, object?>(o.Payload ?? new Dictionary<string, object?>())
        {
            ["from"] = current.Status,
            ["to"] = to,
        };
        await AppendEventAsync(id, o.Action ?? $"status.{to}", o.ActorEmail, o.ActorRole, eventPayload, t);
        _logger?.LogInformation(
            "approval transition id={Id} from={From} to={To}", id, LogSafe.Clean(current.Status), LogSafe.Clean(to));

        if (to == ApprovalStatus.PendingReview)
        {
            await ScheduleReviewTimersAsync(t, updated, now);
        }
        else if (to == ApprovalStatus.NeedsInfo)
        {
            await ScheduleInfoTimerAsync(t, updated);
        }
        return updated;
    });

    // ───────────────────────────── escalate ─────────────────────────────

    /// <summary>
    /// Hands a review up one level: new <c>assigned_role</c>, <c>escalation_level + 1</c>, a fresh deadline and timers. Guarded like
    /// <see cref="TransitionAsync"/>: only a <c>pending_review</c> approval still at <paramref name="expectedLevel"/> moves, and
    /// not while a reviewer holds a live claim (a lower tier must never finish a higher tier's decision). Audited as
    /// <c>escalated</c> with no actor (the system).
    /// </summary>
    public Task<Approval> EscalateAsync(
        Guid id,
        int expectedLevel,
        string assignedRole,
        IReadOnlyDictionary<string, object?>? payload = null,
        Tx? tx = null) => Scoped(tx, async t =>
    {
        if (assignedRole is not ("support" or "admin")) throw new ArgumentException($"unknown approval role: {assignedRole}");
        var now = Approval.TruncateToMicroseconds(DateTime.UtcNow);

        var row = await t.Conn.QueryFirstOrDefaultAsync(
            @"SELECT *, (claimed_at IS NOT NULL AND claimed_at >= NOW() - make_interval(secs => @ttl)) AS claim_live
                FROM approvals WHERE id = @id FOR UPDATE",
            new { id, ttl = (double)_settings.HitlClaimTtlSeconds }, t.Trans);
        if (row is null) throw new ApprovalNotFoundException(id.ToString());
        Approval current = Map(row);
        var claimLive = Col<bool>((IDictionary<string, object>)row, "claim_live");

        if (current.Status != ApprovalStatus.PendingReview || current.EscalationLevel != expectedLevel)
            throw new InvalidTransitionException($"approval {id} is not a pending review at level {expectedLevel}");
        if (claimLive)
            throw new InvalidTransitionException($"approval {id} is being decided; escalation deferred");

        var updatedRow = await t.Conn.QueryFirstAsync(
            @"UPDATE approvals SET assigned_role = @assignedRole, escalation_level = escalation_level + 1,
                                   due_at = @dueAt, updated_at = NOW()
               WHERE id = @id RETURNING *",
            new { id, assignedRole, dueAt = Deadline(current.Kind, now) is { } d ? Approval.AsUtc(d) : (DateTime?)null }, t.Trans);
        Approval updated = Map(updatedRow);

        var eventPayload = new Dictionary<string, object?>(payload ?? new Dictionary<string, object?>())
        {
            ["from_role"] = current.AssignedRole,
            ["to_role"] = assignedRole,
            ["level"] = updated.EscalationLevel,
        };
        await AppendEventAsync(id, "escalated", payload: eventPayload, tx: t);
        _logger?.LogInformation("approval escalated id={Id} level={Level}", id, updated.EscalationLevel);
        await ScheduleReviewTimersAsync(t, updated, now);
        return updated;
    });

    // ───────────────────────────── claim ─────────────────────────────

    /// <summary>
    /// Atomically takes the execution claim on an approval, or returns null if someone else holds it. The claim is the fence for
    /// whoever acts on a decision (resumes a workflow, runs the approved tool). A claim older than <paramref name="ttlSeconds"/>
    /// is stale and may be taken over. Only an approval currently in <paramref name="statuses"/> can be claimed.
    /// </summary>
    public async Task<Approval?> ClaimAsync(Guid id, string claimedBy, IReadOnlyCollection<string> statuses, int ttlSeconds = 600)
    {
        await using var conn = await _pool.OpenAsync();
        var row = await conn.QueryFirstOrDefaultAsync(
            @"UPDATE approvals SET claimed_at = NOW(), claimed_by = @claimedBy, updated_at = NOW()
               WHERE id = @id AND status = ANY(@statuses::text[])
                 AND (claimed_at IS NULL OR claimed_at < NOW() - make_interval(secs => @ttl))
              RETURNING *",
            new { id, claimedBy, statuses = statuses.ToArray(), ttl = (double)ttlSeconds });
        return row is null ? null : Map(row);
    }

    /// <summary>Clears the claim, but only if <paramref name="claimedBy"/> still holds it (a takeover must not be undone).</summary>
    public async Task<bool> ReleaseClaimAsync(Guid id, string claimedBy)
    {
        await using var conn = await _pool.OpenAsync();
        var affected = await conn.ExecuteAsync(
            @"UPDATE approvals SET claimed_at = NULL, claimed_by = NULL, updated_at = NOW()
               WHERE id = @id AND claimed_by = @claimedBy",
            new { id, claimedBy });
        return affected == 1;
    }
}
