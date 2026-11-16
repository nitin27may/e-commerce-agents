using Dapper;
using ECommerceAgents.Shared.Approvals;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Policy;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using System.Text.Json;
using Xunit;
using S = ECommerceAgents.Shared.Approvals.ApprovalStatus;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// D-5n — <see cref="ApprovalRepository"/> against real Postgres: the .NET twin of Python's <c>tests/test_approvals_repo.py</c> and the
/// timer half of <c>test_outbox_worker.py</c>. Every rule here is one the Python worker and the Python stack also rely on, because both
/// stacks write the SAME tables: guarded transitions, the self-approval rule, fenced claims, and the outbox timers an approval writes
/// when it enters review — topics, dedupe keys and payload shape that <c>agents/python/worker/</c> must be able to process.
/// </summary>
[Collection(nameof(LocalPostgresCollection))]
public sealed class ApprovalRepositoryTests : IAsyncLifetime
{
    private const string Requester = "customer@example.com";
    private const string Reviewer = "support@example.com";

    private readonly PostgresFixture _pg;
    private DatabasePool _pool = null!;
    private AgentSettings _settings = null!;
    private ApprovalRepository _repo = null!;

    public ApprovalRepositoryTests(PostgresFixture pg) => _pg = pg;

    public async Task InitializeAsync()
    {
        _settings = new AgentSettings { DatabaseUrl = _pg.ConnectionString };
        _pool = new DatabasePool(_settings);
        _repo = new ApprovalRepository(_pool, _settings, ApprovalPolicyLocator.Current);
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync("TRUNCATE approval_events, approvals, outbox RESTART IDENTITY CASCADE");
    }

    public async Task DisposeAsync() => await _pool.DisposeAsync();

    private Task<Approval> NewReviewAsync(string kind = "return_approval", string requester = Requester, Guid? sourceRef = null) =>
        _repo.CreateAsync(new NewApproval(
            Source: "workflow",
            Kind: kind,
            RequesterEmail: requester,
            SourceRef: sourceRef ?? Guid.NewGuid(),
            Amount: 80m,
            PolicyVersion: "test",
            Route: "support",
            AssignedRole: "support",
            Status: S.PendingReview));

    private async Task<List<(string Topic, string DedupeKey, DateTime AvailableAt, JsonElement Payload)>> TimersAsync(Guid approvalId)
    {
        await using var conn = await _pool.OpenAsync();
        var rows = await conn.QueryAsync(
            "SELECT topic, dedupe_key, available_at, payload::text AS payload FROM outbox WHERE aggregate_id = @approvalId ORDER BY id",
            new { approvalId });
        return rows.Select(r => ((string)r.topic, (string)r.dedupe_key, DateTime.SpecifyKind((DateTime)r.available_at, DateTimeKind.Utc),
            JsonDocument.Parse((string)r.payload).RootElement.Clone())).ToList();
    }

    // ───────────────────────────── create ─────────────────────────────

    [Fact]
    public async Task Create_WritesTheApprovalAndACreatedEvent_AndIsIdempotentPerSource()
    {
        var sourceRef = Guid.NewGuid();
        var first = await NewReviewAsync(sourceRef: sourceRef);
        var second = await NewReviewAsync(sourceRef: sourceRef);

        second.Id.Should().Be(first.Id, "a retried creator gets the existing row back");
        var events = await _repo.ListEventsAsync(first.Id);
        events.Select(e => e.Action).Should().Equal("created");
        (await TimersAsync(first.Id)).Should().HaveCount(2, "no second pair of timers for the retry");
    }

    [Fact]
    public async Task Create_RejectsAnUnknownSourceOrRoute()
    {
        var bad = () => _repo.CreateAsync(new NewApproval("nope", "x", Requester));
        await bad.Should().ThrowAsync<ArgumentException>();
        var badRoute = () => _repo.CreateAsync(new NewApproval("tool", "x", Requester, Route: "nobody"));
        await badRoute.Should().ThrowAsync<ArgumentException>();
    }

    // ───────────────────────────── timers (what the Python worker reads) ─────────────────────────────

    /// <summary>
    /// The contract with <c>agents/python/worker/</c>: an approval entering <c>pending_review</c> gets <c>approval.reminder_due</c> and
    /// <c>approval.sla_breached</c> rows whose dedupe key is <c>{topic}:{id}:{level}:{epoch ms of due_at}</c> and whose payload is
    /// <c>{approval_id, level, due_at}</c> with <c>due_at</c> an ISO-8601 instant equal to the approval's own.
    /// </summary>
    [Fact]
    public async Task EnteringReview_WritesTheSameTimerRowsPythonWrites()
    {
        var approval = await NewReviewAsync();

        approval.DueAt.Should().NotBeNull();
        var timers = await TimersAsync(approval.Id);
        timers.Select(t => t.Topic).Should().BeEquivalentTo(
            new[] { ApprovalRepository.TopicReminderDue, ApprovalRepository.TopicSlaBreached });

        var due = approval.DueAt!.Value;
        var stamp = (long)Math.Floor((due - DateTime.UnixEpoch).TotalMilliseconds);
        foreach (var (topic, key, availableAt, payload) in timers)
        {
            key.Should().Be($"{topic}:{approval.Id}:0:{stamp}");
            payload.GetProperty("approval_id").GetString().Should().Be(approval.Id.ToString());
            payload.GetProperty("level").GetInt32().Should().Be(0);
            // The worker compares this as an instant with the approval's due_at: it must round-trip exactly (microseconds, +00:00).
            var text = payload.GetProperty("due_at").GetString()!;
            text.Should().MatchRegex(@"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\+00:00$", "Python's datetime.fromisoformat reads exactly this");
            DateTimeOffset.Parse(text).UtcDateTime.Should().Be(due);
            if (topic == ApprovalRepository.TopicSlaBreached) availableAt.Should().Be(due);
        }

        var reminder = timers.Single(t => t.Topic == ApprovalRepository.TopicReminderDue).AvailableAt;
        var createdAt = approval.CreatedAt;
        reminder.Should().BeAfter(createdAt).And.BeBefore(due, "the reminder fires part-way to the deadline");
        // sla_hours for return_approval is 24: the deadline is a day out, the reminder about half way.
        (due - createdAt).TotalHours.Should().BeApproximately(24, 0.1);
        (reminder - createdAt).TotalHours.Should().BeApproximately(12, 0.1);
    }

    [Fact]
    public async Task NeedsInfo_WritesOnlyABreachTimer_AndAnAnswerStartsAFreshClockWithItsOwnTimers()
    {
        var approval = await NewReviewAsync();
        var asked = await _repo.TransitionAsync(approval.Id, [S.PendingReview], S.NeedsInfo,
            new TransitionOptions { ActorEmail = Reviewer, ActorRole = "support", Action = "info_requested" });

        (await TimersAsync(approval.Id)).Should().HaveCount(3, "the review pair plus one breach timer for needs_info");
        var infoTimer = (await TimersAsync(approval.Id)).Last();
        infoTimer.Topic.Should().Be(ApprovalRepository.TopicSlaBreached);
        asked.DueAt.Should().NotBeNull();

        await Task.Delay(20);
        var answered = await _repo.TransitionAsync(approval.Id, [S.NeedsInfo], S.PendingReview,
            new TransitionOptions { ActorEmail = Requester, Action = "info_provided" });

        answered.DueAt.Should().BeAfter(asked.DueAt!.Value, "an answer starts a fresh clock");
        (await TimersAsync(approval.Id)).Should().HaveCount(5, "a fresh reminder and breach pair; the old ones stay and recognise themselves as stale");
    }

    [Fact]
    public async Task ARolledBackApproval_LeavesNoTimer()
    {
        var sourceRef = Guid.NewGuid();
        var act = () => _repo.InTransactionAsync<int>(async tx =>
        {
            await _repo.CreateAsync(new NewApproval("workflow", "return_approval", Requester, sourceRef, Status: S.PendingReview, AssignedRole: "support"), tx);
            throw new InvalidOperationException("boom");
        });
        await act.Should().ThrowAsync<InvalidOperationException>();

        await using var conn = await _pool.OpenAsync();
        (await conn.ExecuteScalarAsync<long>("SELECT count(*) FROM approvals WHERE source_ref = @sourceRef", new { sourceRef })).Should().Be(0);
        (await conn.ExecuteScalarAsync<long>("SELECT count(*) FROM outbox")).Should().Be(0);
    }

    [Fact]
    public async Task AKindTheLoadedPolicyDoesNotKnow_GetsNoDeadlineAndNoTimers()
    {
        var approval = await NewReviewAsync(kind: "some_future_tool");
        approval.DueAt.Should().BeNull();
        (await TimersAsync(approval.Id)).Should().BeEmpty();
    }

    // ───────────────────────────── guarded transitions ─────────────────────────────

    [Fact]
    public async Task Transition_MovesOnlyFromTheNamedStates_AndAuditsTheMove()
    {
        var approval = await NewReviewAsync();

        var approved = await _repo.TransitionAsync(approval.Id, [S.PendingReview], S.Approved,
            new TransitionOptions { ActorEmail = Reviewer, ActorRole = "support", Action = "decided", DecidedBy = Reviewer });
        approved.Status.Should().Be(S.Approved);
        approved.DecidedBy.Should().Be(Reviewer);
        approved.DecidedAt.Should().NotBeNull();

        var again = () => _repo.TransitionAsync(approval.Id, [S.PendingReview], S.Rejected);
        await again.Should().ThrowAsync<InvalidTransitionException>("it is no longer pending review");

        var events = await _repo.ListEventsAsync(approval.Id);
        events.Select(e => e.Action).Should().Equal("created", "decided");
        events[1].Payload.GetProperty("from").GetString().Should().Be(S.PendingReview);
        events[1].Payload.GetProperty("to").GetString().Should().Be(S.Approved);
    }

    [Theory]
    [InlineData(S.Approved, S.PendingReview)]
    [InlineData(S.Executed, S.Approved)]
    [InlineData(S.Rejected, S.Approved)]
    [InlineData(S.PendingReview, S.Executed)]
    public async Task Transition_RefusesAnEdgeThatIsNotInTheLifecycle(string from, string to)
    {
        var approval = await NewReviewAsync();
        var act = () => _repo.TransitionAsync(approval.Id, [from], to);
        await act.Should().ThrowAsync<ArgumentException>();
    }

    [Fact]
    public async Task Transition_OfAnUnknownApproval_IsNotFound()
    {
        var act = () => _repo.TransitionAsync(Guid.NewGuid(), [S.PendingReview], S.Approved);
        await act.Should().ThrowAsync<ApprovalNotFoundException>();
    }

    [Theory]
    [InlineData("customer@example.com")]
    [InlineData("CUSTOMER@example.com")]
    [InlineData("  customer@example.com ")]
    public async Task NobodyDecidesTheirOwnRequest_CaseInsensitively_AndNothingChanges(string decidedBy)
    {
        var approval = await NewReviewAsync();
        var act = () => _repo.TransitionAsync(approval.Id, [S.PendingReview], S.Approved,
            new TransitionOptions { DecidedBy = decidedBy });
        await act.Should().ThrowAsync<SelfApprovalException>();
        (await _repo.GetAsync(approval.Id))!.Status.Should().Be(S.PendingReview);
    }

    [Fact]
    public async Task TheDatabaseItselfRefusesASelfApproval()
    {
        var approval = await NewReviewAsync();
        await using var conn = await _pool.OpenAsync();
        var act = () => conn.ExecuteAsync(
            "UPDATE approvals SET decided_by = @who WHERE id = @id", new { who = Requester.ToUpperInvariant(), id = approval.Id });
        await act.Should().ThrowAsync<Npgsql.PostgresException>().Where(e => e.SqlState == "23514");
    }

    [Fact]
    public async Task TheAuditTrailIsAppendOnly()
    {
        var approval = await NewReviewAsync();
        await using var conn = await _pool.OpenAsync();
        var update = () => conn.ExecuteAsync("UPDATE approval_events SET action = 'x' WHERE approval_id = @id", new { id = approval.Id });
        await update.Should().ThrowAsync<Npgsql.PostgresException>();
        var delete = () => conn.ExecuteAsync("DELETE FROM approval_events WHERE approval_id = @id", new { id = approval.Id });
        await delete.Should().ThrowAsync<Npgsql.PostgresException>();
    }

    // ───────────────────────────── claim / fencing ─────────────────────────────

    [Fact]
    public async Task OnlyOneCallerTakesTheClaim_AndAFreshClaimIsNotTakenOver()
    {
        var approval = await NewReviewAsync();
        (await _repo.ClaimAsync(approval.Id, "a", [S.PendingReview], 600)).Should().NotBeNull();
        (await _repo.ClaimAsync(approval.Id, "b", [S.PendingReview], 600)).Should().BeNull();
        (await _repo.GetAsync(approval.Id))!.ClaimedBy.Should().Be("a");
    }

    [Fact]
    public async Task AClaimOnlyAppliesInTheNamedStatuses()
    {
        var approval = await NewReviewAsync();
        (await _repo.ClaimAsync(approval.Id, "a", [S.NeedsInfo], 600)).Should().BeNull();
    }

    [Fact]
    public async Task AStaleClaim_IsTakenOver_AndTheOldHoldersTransitionIsFencedOut()
    {
        var approval = await NewReviewAsync();
        await _repo.ClaimAsync(approval.Id, "old", [S.PendingReview], 600);
        await using (var conn = await _pool.OpenAsync())
        {
            await conn.ExecuteAsync("UPDATE approvals SET claimed_at = NOW() - interval '1 hour' WHERE id = @id", new { id = approval.Id });
        }

        (await _repo.ClaimAsync(approval.Id, "new", [S.PendingReview], 600)).Should().NotBeNull("a claim older than the TTL is stale");

        var lost = () => _repo.TransitionAsync(approval.Id, [S.PendingReview], S.Approved,
            new TransitionOptions { ClaimedBy = "old", DecidedBy = Reviewer });
        await lost.Should().ThrowAsync<ClaimLostException>();
        (await _repo.GetAsync(approval.Id))!.Status.Should().Be(S.PendingReview, "the stalled holder changed nothing");

        var won = await _repo.TransitionAsync(approval.Id, [S.PendingReview], S.Approved,
            new TransitionOptions { ClaimedBy = "new", DecidedBy = Reviewer });
        won.Status.Should().Be(S.Approved);
        won.ClaimedBy.Should().BeNull("a successful fenced move clears the claim");
    }

    [Fact]
    public async Task ReleaseOnlyClearsTheCallersOwnClaim()
    {
        var approval = await NewReviewAsync();
        await _repo.ClaimAsync(approval.Id, "a", [S.PendingReview], 600);

        (await _repo.ReleaseClaimAsync(approval.Id, "someone-else")).Should().BeFalse("a takeover must not be undone");
        (await _repo.GetAsync(approval.Id))!.ClaimedBy.Should().Be("a");
        (await _repo.ReleaseClaimAsync(approval.Id, "a")).Should().BeTrue();
        (await _repo.GetAsync(approval.Id))!.ClaimedAt.Should().BeNull();
        (await _repo.ClaimAsync(approval.Id, "b", [S.PendingReview], 600)).Should().NotBeNull("a released approval is claimable again");
    }

    // ───────────────────────────── escalate ─────────────────────────────

    [Fact]
    public async Task Escalate_HandsTheReviewUpOnce_WithAFreshDeadlineAndTimers()
    {
        var approval = await NewReviewAsync();

        var escalated = await _repo.EscalateAsync(approval.Id, expectedLevel: 0, assignedRole: "admin");

        escalated.AssignedRole.Should().Be("admin");
        escalated.EscalationLevel.Should().Be(1);
        escalated.Status.Should().Be(S.PendingReview);
        (await TimersAsync(approval.Id)).Should().HaveCount(4, "a fresh pair at level 1");
        (await TimersAsync(approval.Id)).Last().Payload.GetProperty("level").GetInt32().Should().Be(1);

        var redelivered = () => _repo.EscalateAsync(approval.Id, expectedLevel: 0, assignedRole: "admin");
        await redelivered.Should().ThrowAsync<InvalidTransitionException>("a redelivered breach cannot escalate twice");

        var events = await _repo.ListEventsAsync(approval.Id);
        events.Last().Action.Should().Be("escalated");
        events.Last().ActorEmail.Should().BeNull("the system escalates");
    }

    [Fact]
    public async Task Escalate_IsDeferredWhileAReviewerHoldsALiveClaim()
    {
        var approval = await NewReviewAsync();
        await _repo.ClaimAsync(approval.Id, "reviewer", [S.PendingReview], 600);

        var act = () => _repo.EscalateAsync(approval.Id, 0, "admin");
        await act.Should().ThrowAsync<InvalidTransitionException>("a lower tier must not finish a higher tier's decision");
        (await _repo.GetAsync(approval.Id))!.AssignedRole.Should().Be("support");
    }

    // ───────────────────────────── logging ─────────────────────────────

    /// <summary>A kind or source can come from a request; whatever the repository logs about it must not carry a line break (log forging).</summary>
    [Fact]
    public async Task RequestDerivedValues_AreCleanedBeforeTheyReachALogLine()
    {
        var sink = new CapturingLogger();
        var repo = new ApprovalRepository(_pool, _settings, ApprovalPolicyLocator.Current, sink);
        var hostile = "cancel_order\r\n2026-11-16 ERROR forged line";

        var approval = await repo.CreateAsync(new NewApproval(
            "tool", hostile, Requester, Guid.NewGuid(), Status: S.PendingReview, AssignedRole: "support"));
        await repo.TransitionAsync(approval.Id, [S.PendingReview], S.Rejected, new TransitionOptions { DecidedBy = Reviewer });

        sink.Lines.Should().NotBeEmpty();
        sink.Lines.Should().OnlyContain(l => !l.Contains('\r') && !l.Contains('\n'), "LogSafe.Clean strips CR and LF");
        sink.Lines.Should().Contain(l => l.Contains("approval created") && l.Contains("forged line"), "the text is kept, only the line break is gone");
    }

    private sealed class CapturingLogger : Microsoft.Extensions.Logging.ILogger
    {
        public List<string> Lines { get; } = [];
        public IDisposable? BeginScope<TState>(TState state) where TState : notnull => null;
        public bool IsEnabled(Microsoft.Extensions.Logging.LogLevel logLevel) => true;
        public void Log<TState>(
            Microsoft.Extensions.Logging.LogLevel logLevel, Microsoft.Extensions.Logging.EventId eventId, TState state,
            Exception? exception, Func<TState, Exception?, string> formatter) => Lines.Add(formatter(state, exception));
    }

    // ───────────────────────────── queries ─────────────────────────────

    [Fact]
    public async Task TheStaffQueue_IsOldestDeadlineFirst_AndRoleScoped()
    {
        var cancel = await NewReviewAsync(kind: "cancel_order");   // 12h
        var ret = await NewReviewAsync(kind: "return_approval");   // 24h
        var manager = await _repo.CreateAsync(new NewApproval(
            "workflow", "return_approval", Requester, Guid.NewGuid(), Route: "manager", AssignedRole: "admin", Status: S.PendingReview));

        var all = await _repo.ListForStaffAsync([S.PendingReview], null);
        all.Select(a => a.Id).Should().Contain([cancel.Id, ret.Id, manager.Id]);
        all.First().Id.Should().Be(cancel.Id, "the nearest deadline comes first");

        var support = await _repo.ListForStaffAsync([S.PendingReview], ["support"]);
        support.Select(a => a.Id).Should().NotContain(manager.Id);
    }

    [Fact]
    public async Task ARequestersApprovals_AreMatchedCaseInsensitively()
    {
        var approval = await NewReviewAsync(requester: "Mixed.Case@Example.com");
        (await _repo.ListForRequesterAsync("mixed.case@example.COM")).Select(a => a.Id).Should().Contain(approval.Id);
        (await _repo.ListForRequesterAsync("someone.else@example.com")).Should().BeEmpty();
    }
}
