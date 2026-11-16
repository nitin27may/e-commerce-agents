using System.Globalization;
using System.Text.Json;

namespace ECommerceAgents.Shared.Approvals;

/// <summary>The lifecycle states of an <c>approvals</c> row — the same strings the table's CHECK allows.</summary>
public static class ApprovalStatus
{
    public const string PendingConfirm = "pending_confirm";
    public const string PendingReview = "pending_review";
    public const string NeedsInfo = "needs_info";
    public const string Approved = "approved";
    public const string ApprovedWithEdits = "approved_with_edits";
    public const string Rejected = "rejected";
    public const string Expired = "expired";
    public const string Executed = "executed";
    public const string Failed = "failed";

    /// <summary>
    /// The lifecycle. A status absent from the keys (or mapped to an empty set) is terminal.
    /// Identical to Python's <c>ALLOWED_TRANSITIONS</c>.
    /// </summary>
    public static readonly IReadOnlyDictionary<string, IReadOnlySet<string>> AllowedTransitions =
        new Dictionary<string, IReadOnlySet<string>>
        {
            [PendingConfirm] = new HashSet<string> { PendingReview, Approved, Rejected, Expired },
            [PendingReview] = new HashSet<string> { NeedsInfo, Approved, ApprovedWithEdits, Rejected, Expired },
            [NeedsInfo] = new HashSet<string> { PendingReview, Rejected, Expired },
            [Approved] = new HashSet<string> { Executed, Failed },
            [ApprovedWithEdits] = new HashSet<string> { Executed, Failed },
            [Rejected] = new HashSet<string>(),
            [Expired] = new HashSet<string>(),
            [Executed] = new HashSet<string>(),
            [Failed] = new HashSet<string>(),
        };

    public static bool IsKnown(string? status) => status is not null && AllowedTransitions.ContainsKey(status);
}

/// <summary>Base class for approval repository failures.</summary>
public class ApprovalException(string message) : Exception(message);

/// <summary>No approval has that id.</summary>
public sealed class ApprovalNotFoundException(string message) : ApprovalException(message);

/// <summary>The approval is not in one of the states the caller said it must be in.</summary>
public class InvalidTransitionException(string message) : ApprovalException(message);

/// <summary><c>decided_by</c> equals <c>requester_email</c>: nobody decides their own request.</summary>
public sealed class SelfApprovalException(string message) : ApprovalException(message);

/// <summary>The caller's execution claim was taken over (or cleared) before it finished.</summary>
public sealed class ClaimLostException(string message) : InvalidTransitionException(message);

/// <summary>
/// One row of <c>approvals</c>. JSON columns stay as <see cref="JsonElement"/> so their keys are never
/// rewritten by a serializer naming policy.
/// </summary>
public sealed record Approval(
    Guid Id,
    string Source,
    Guid? SourceRef,
    string Kind,
    string RequesterEmail,
    string? SubjectEmail,
    Guid? ConversationId,
    decimal? Amount,
    string? PolicyVersion,
    string? Route,
    string? AssignedRole,
    string Status,
    JsonElement? Recommendation,
    JsonElement? Decision,
    string? DecidedBy,
    DateTime? DecidedAt,
    DateTime? DueAt,
    int EscalationLevel,
    DateTime? ClaimedAt,
    string? ClaimedBy,
    DateTime CreatedAt,
    DateTime UpdatedAt
)
{
    /// <summary>
    /// The row as the API serialises it: Python's <c>jsonable(row)</c> — UUIDs and datetimes as strings, the
    /// amount as a number, every column present.
    /// </summary>
    public Dictionary<string, object?> ToJson() => new()
    {
        ["id"] = Id.ToString(),
        ["source"] = Source,
        ["source_ref"] = SourceRef?.ToString(),
        ["kind"] = Kind,
        ["requester_email"] = RequesterEmail,
        ["subject_email"] = SubjectEmail,
        ["conversation_id"] = ConversationId?.ToString(),
        ["amount"] = Amount is null ? null : (double)Amount.Value,
        ["policy_version"] = PolicyVersion,
        ["route"] = Route,
        ["assigned_role"] = AssignedRole,
        ["status"] = Status,
        ["recommendation"] = Recommendation,
        ["decision"] = Decision,
        ["decided_by"] = DecidedBy,
        ["decided_at"] = Iso(DecidedAt),
        ["due_at"] = Iso(DueAt),
        ["escalation_level"] = EscalationLevel,
        ["claimed_at"] = Iso(ClaimedAt),
        ["claimed_by"] = ClaimedBy,
        ["created_at"] = Iso(CreatedAt),
        ["updated_at"] = Iso(UpdatedAt),
    };

    /// <summary>
    /// A timestamp the way Python's <c>datetime.isoformat()</c> writes a UTC value with microseconds
    /// (<c>2026-11-16T10:00:00.123456+00:00</c>). The worker compares an outbox payload's <c>due_at</c> with the
    /// approval's, as instants, so this must round-trip exactly.
    /// </summary>
    public static string? Iso(DateTime? value) => value is null ? null : Iso(value.Value);

    public static string Iso(DateTime value) =>
        TruncateToMicroseconds(AsUtc(value)).ToString("yyyy-MM-dd'T'HH:mm:ss.ffffff'+00:00'", CultureInfo.InvariantCulture);

    /// <summary>Postgres stores microseconds; a .NET tick is 100 ns. Trim so what we write is what we read back.</summary>
    public static DateTime TruncateToMicroseconds(DateTime value) =>
        new(value.Ticks - (value.Ticks % 10), value.Kind);

    public static DateTime AsUtc(DateTime value) => value.Kind switch
    {
        DateTimeKind.Utc => value,
        DateTimeKind.Local => value.ToUniversalTime(),
        _ => DateTime.SpecifyKind(value, DateTimeKind.Utc),
    };
}

/// <summary>One audit row (<c>approval_events</c>).</summary>
public sealed record ApprovalEvent(
    long Id,
    Guid ApprovalId,
    string? ActorEmail,
    string? ActorRole,
    string Action,
    JsonElement Payload,
    DateTime CreatedAt
)
{
    public Dictionary<string, object?> ToJson() => new()
    {
        ["id"] = Id,
        ["approval_id"] = ApprovalId.ToString(),
        ["actor_email"] = ActorEmail,
        ["actor_role"] = ActorRole,
        ["action"] = Action,
        ["payload"] = Payload,
        ["created_at"] = Approval.Iso(CreatedAt),
    };
}
