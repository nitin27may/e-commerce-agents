using Dapper;
using ECommerceAgents.Orchestrator.Approvals;
using ECommerceAgents.Orchestrator.Modes;
using ECommerceAgents.Shared.Approvals;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Orchestration;
using ECommerceAgents.Shared.Policy;
using ECommerceAgents.Shared.Workflows;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging.Abstractions;
using System.Text.Json;
using Xunit;
using S = ECommerceAgents.Shared.Approvals.ApprovalStatus;

namespace ECommerceAgents.Orchestrator.Tests;

/// <summary>
/// NET-36 (parity row 36) — the .NET consumer of <c>approval.timeout_apply</c>. Real Postgres, the real repository, intake, service and
/// outbox SQL; only the workflow mode is a stub (the checkpoint resume itself is covered by <c>ReturnReplaceCheckpointResumeTests</c>).
/// </summary>
[Collection(nameof(LocalPostgresCollection))]
public sealed class ApprovalTimeoutWorkerTests : IAsyncLifetime
{
    private const string Customer = "cust@example.com";
    private const string Reviewer = "support@example.com";

    private readonly PostgresFixture _pg;
    private DatabasePool _pool = null!;
    private AgentSettings _settings = null!;
    private ApprovalRepository _repo = null!;
    private ApprovalIntake _intake = null!;

    public ApprovalTimeoutWorkerTests(PostgresFixture pg) => _pg = pg;

    public async Task InitializeAsync()
    {
        _settings = Settings();
        _pool = new DatabasePool(_settings);
        _repo = new ApprovalRepository(_pool, _settings, ApprovalPolicyLocator.Current);
        _intake = new ApprovalIntake(_pool, _repo, ApprovalPolicyLocator.Current);
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            @"TRUNCATE approval_events, approvals, outbox, notifications, tool_approval_requests, hitl_requests,
                       workflow_checkpoints, usage_logs, returns, order_status_history, order_items, orders, users
              RESTART IDENTITY CASCADE");
        foreach (var (email, role) in new[] { (Customer, "customer"), (Reviewer, "support") })
            await conn.ExecuteAsync(
                "INSERT INTO users (email, password_hash, name, role) VALUES (@email, 'x', 'T', @role)", new { email, role });
    }

    public async Task DisposeAsync() => await _pool.DisposeAsync();

    private AgentSettings Settings(Func<AgentSettings, AgentSettings>? tweak = null)
    {
        var settings = new AgentSettings
        {
            DatabaseUrl = _pg.ConnectionString,
            OutboxMaxAttempts = 3,
            OutboxBackoffBaseSeconds = 0,
            OutboxBackoffMaxSeconds = 0,
        };
        return tweak?.Invoke(settings) ?? settings;
    }

    // ───────────────────────────── seeding ─────────────────────────────

    private sealed record Seeded(Guid HitlId, Approval Approval);

    /// <summary>A paused return and its inbox row, opened by the .NET intake (so tagged <c>dotnet</c>).</summary>
    private async Task<Seeded> SeedPausedReturnAsync(decimal amount = 80m)
    {
        await using var conn = await _pool.OpenAsync();
        var userId = await conn.ExecuteScalarAsync<Guid>("SELECT id FROM users WHERE email = @Customer", new { Customer });
        var runId = await conn.ExecuteScalarAsync<Guid>(
            "INSERT INTO usage_logs (user_id, agent_name, input_summary, status) VALUES (@userId, 'orchestrator', 'return', 'success') RETURNING id",
            new { userId });
        var checkpointId = Guid.NewGuid();
        await conn.ExecuteAsync(
            "INSERT INTO workflow_checkpoints (checkpoint_id, workflow_name, payload, session_id, usage_log_id) VALUES (@checkpointId, 'return-replace', '{}'::jsonb, 'sess-1', @runId)",
            new { checkpointId, runId });
        var hitlId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO hitl_requests (workflow_run_id, request_id, checkpoint_id, user_email, kind, payload, status)
              VALUES (@runId, 'req-1', @checkpointId, @Customer, 'return_approval', @payload::jsonb, 'pending') RETURNING id",
            new { runId, checkpointId, Customer, payload = JsonSerializer.Serialize(new { session_id = "sess-1" }) });
        var approval = await _intake.OpenWorkflowApprovalAsync(
            hitlId, Customer, null, new RoutedPolicy("support", ["test rule"], new Dictionary<string, object?>(), "test-policy", amount));
        return new Seeded(hitlId, approval);
    }

    /// <summary>What the Python breach handler writes at the top of the chain: one due <c>approval.timeout_apply</c> row.</summary>
    private async Task<long> EnqueueTimeoutAsync(Approval approval, string target = "expired", int? level = null, bool due = true)
    {
        await using var conn = await _pool.OpenAsync();
        var payload = JsonSerializer.Serialize(new
        {
            approval_id = approval.Id.ToString(),
            level = level ?? approval.EscalationLevel,
            due_at = approval.DueAt?.ToString("O"),
            target,
        });
        return await conn.ExecuteScalarAsync<long>(
            @"INSERT INTO outbox (topic, aggregate_id, payload, available_at)
              VALUES ('approval.timeout_apply', @id, @payload::jsonb, NOW() - make_interval(secs => @ago)) RETURNING id",
            new { id = approval.Id, payload, ago = due ? 60.0 : -3600.0 });
    }

    private (ApprovalTimeoutProcessor Processor, StubMode Mode) NewProcessor(StubMode? mode = null, AgentSettings? settings = null)
    {
        mode ??= new StubMode();
        settings ??= _settings;
        var services = new ServiceCollection();
        services.AddSingleton<IOrchestrationMode>(mode);
        services.AddSingleton<ModeRegistry>();
        var sp = services.BuildServiceProvider();
        var repo = new ApprovalRepository(_pool, settings, ApprovalPolicyLocator.Current);
        var intake = new ApprovalIntake(_pool, repo, ApprovalPolicyLocator.Current);
        var service = new ApprovalsService(
            _pool, repo, intake, ApprovalPolicyLocator.Current, settings, sp, NullLogger<ApprovalsService>.Instance);
        return (new ApprovalTimeoutProcessor(_pool, repo, service, settings, NullLogger<ApprovalTimeoutProcessor>.Instance), mode);
    }

    private async Task<dynamic> OutboxRowAsync(long id)
    {
        await using var conn = await _pool.OpenAsync();
        return await conn.QueryFirstAsync("SELECT * FROM outbox WHERE id = @id", new { id });
    }

    private async Task<string> HitlStatusAsync(Guid hitlId)
    {
        await using var conn = await _pool.OpenAsync();
        return await conn.ExecuteScalarAsync<string>("SELECT status FROM hitl_requests WHERE id = @hitlId", new { hitlId }) ?? "";
    }

    private async Task<long> ReturnCountAsync()
    {
        await using var conn = await _pool.OpenAsync();
        return await conn.ExecuteScalarAsync<long>("SELECT count(*) FROM returns");
    }

    // ───────────────────────────── tagging ─────────────────────────────

    [Fact]
    public async Task APausedReturnTheDotnetStackOpens_IsTaggedDotnet_AndAToolApprovalIsNot()
    {
        var seeded = await SeedPausedReturnAsync();
        seeded.Approval.Recommendation!.Value.GetProperty("stack").GetString().Should().Be("dotnet");
        seeded.Approval.Recommendation!.Value.GetProperty("route").GetString().Should().Be("support", "the tag is additive");

        await using var conn = await _pool.OpenAsync();
        var requestId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO tool_approval_requests (user_email, agent_name, tool_name, tool_input)
              VALUES (@Customer, 'order-management', 'cancel_order', '{""order_id"": ""00000000-0000-0000-0000-000000000001""}'::jsonb) RETURNING id",
            new { Customer });
        using var input = JsonDocument.Parse("""{"order_id": "00000000-0000-0000-0000-000000000001"}""");
        var tool = await _intake.OpenToolApprovalAsync(requestId, Customer, null, "CancelOrder", input.RootElement.Clone());
        tool.Recommendation!.Value.TryGetProperty("stack", out _).Should().BeFalse("a tool approval expires through the DB only, on either stack");
    }

    // ───────────────────────────── the happy path ─────────────────────────────

    [Fact]
    public async Task ATimedOutReturn_IsResumedAsARejection_AsTheRequester_AndExpires_WithNoReturnRowCreated()
    {
        var seeded = await SeedPausedReturnAsync();
        var rowId = await EnqueueTimeoutAsync(seeded.Approval);
        var (processor, mode) = NewProcessor();

        var tick = await processor.RunOnceAsync("w1");

        tick.Should().Be(new TimeoutTick(1, 1, 0, 0, 0));
        mode.Calls.Should().Be(1);
        mode.LastDecision!.Decision.Should().Be("reject");
        mode.LastDecision.DecidedBy.Should().Be("system", "the reviewer is recorded separately from the requester");
        mode.LastDecision.ApprovalId.Should().Be(seeded.Approval.Id.ToString());
        mode.SeenEmail.Should().Be(Customer, "the run executes as the original requester");
        mode.SeenRole.Should().Be("customer");

        (await _repo.GetAsync(seeded.Approval.Id))!.Should().Match<Approval>(a => a.Status == S.Expired && a.ClaimedBy == null);
        (await HitlStatusAsync(seeded.HitlId)).Should().Be("rejected");
        (await ReturnCountAsync()).Should().Be(0, "a timed-out return is a rejection: nothing was created");
        var events = (await _repo.ListEventsAsync(seeded.Approval.Id)).Select(e => e.Action).ToList();
        events.Should().Contain("sla_timeout");
        ((object?)(await OutboxRowAsync(rowId)).processed_at).Should().NotBeNull();

        await using var conn = await _pool.OpenAsync();
        var note = await conn.QueryFirstAsync("SELECT user_email, kind FROM notifications WHERE approval_id = @id", new { id = seeded.Approval.Id });
        ((string)note.user_email).Should().Be(Customer);
        ((string)note.kind).Should().Be("approval.timed_out");
    }

    [Fact]
    public async Task OnTimeoutReject_EndsTheApprovalAsRejected()
    {
        var seeded = await SeedPausedReturnAsync();
        await EnqueueTimeoutAsync(seeded.Approval, target: "rejected");
        var (processor, _) = NewProcessor();

        await processor.RunOnceAsync("w1");

        (await _repo.GetAsync(seeded.Approval.Id))!.Status.Should().Be(S.Rejected);
    }

    [Fact]
    public async Task AParkedNeedsInfoReturn_IsUnparkedAndRejected()
    {
        var seeded = await SeedPausedReturnAsync();
        await EnqueueTimeoutAsync(seeded.Approval);
        await using (var conn = await _pool.OpenAsync())
        {
            await conn.ExecuteAsync("UPDATE hitl_requests SET status = 'needs_info' WHERE id = @id", new { id = seeded.HitlId });
            await conn.ExecuteAsync("UPDATE approvals SET status = 'needs_info' WHERE id = @id", new { id = seeded.Approval.Id });
        }
        var (processor, mode) = NewProcessor();

        await processor.RunOnceAsync("w1");

        mode.Calls.Should().Be(1);
        (await _repo.GetAsync(seeded.Approval.Id))!.Status.Should().Be(S.Expired);
        (await HitlStatusAsync(seeded.HitlId)).Should().Be("rejected");
    }

    // ───────────────────────────── routing ─────────────────────────────

    [Fact]
    public async Task APausedReturnPythonOpened_IsNotClaimed_AndItsRowStaysForThePythonWorker()
    {
        var seeded = await SeedPausedReturnAsync();
        await using (var conn = await _pool.OpenAsync())
            await conn.ExecuteAsync(
                "UPDATE approvals SET recommendation = recommendation - 'stack' WHERE id = @id", new { id = seeded.Approval.Id });
        var rowId = await EnqueueTimeoutAsync(seeded.Approval);
        var (processor, mode) = NewProcessor();

        var tick = await processor.RunOnceAsync("w1");

        tick.Claimed.Should().Be(0);
        mode.Calls.Should().Be(0);
        var row = await OutboxRowAsync(rowId);
        ((object?)row.processed_at).Should().BeNull();
        ((int)row.attempts).Should().Be(0);
        ((object?)row.locked_by).Should().BeNull();
    }

    [Fact]
    public async Task OtherTopics_AreNeverClaimed()
    {
        var seeded = await SeedPausedReturnAsync();
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            @"INSERT INTO outbox (topic, aggregate_id, payload, available_at)
              VALUES ('approval.sla_breached', @id, '{}'::jsonb, NOW() - interval '1 minute'),
                     ('approval.reminder_due', @id, '{}'::jsonb, NOW() - interval '1 minute')",
            new { id = seeded.Approval.Id });
        // The two timers .NET itself wrote when the approval entered review are there too.
        var (processor, _) = NewProcessor();

        (await processor.RunOnceAsync("w1")).Claimed.Should().Be(0);
    }

    [Fact]
    public async Task ATimeoutThatIsNotYetDue_IsLeftAlone()
    {
        var seeded = await SeedPausedReturnAsync();
        await EnqueueTimeoutAsync(seeded.Approval, due: false);
        var (processor, mode) = NewProcessor();

        (await processor.RunOnceAsync("w1")).Claimed.Should().Be(0);
        mode.Calls.Should().Be(0);
    }

    // ───────────────────────────── fencing ─────────────────────────────

    [Fact]
    public async Task ADecideInFlight_Wins_TheTimeoutBacksOffWithoutResumingAnything()
    {
        var seeded = await SeedPausedReturnAsync();
        var rowId = await EnqueueTimeoutAsync(seeded.Approval);
        var held = await _repo.ClaimAsync(seeded.Approval.Id, "reviewer-token", [S.PendingReview], 600);
        held.Should().NotBeNull();
        var (processor, mode) = NewProcessor();

        var tick = await processor.RunOnceAsync("w1");

        tick.Should().Be(new TimeoutTick(1, 1, 0, 0, 0), "a decide in flight is not a failure: the timeout row is finished, the decide owns the outcome");
        mode.Calls.Should().Be(0);
        var after = (await _repo.GetAsync(seeded.Approval.Id))!;
        after.Status.Should().Be(S.PendingReview);
        after.ClaimedBy.Should().Be("reviewer-token", "the decide's claim is untouched");
        (await HitlStatusAsync(seeded.HitlId)).Should().Be("pending");
        ((object?)(await OutboxRowAsync(rowId)).processed_at).Should().NotBeNull();
    }

    [Fact]
    public async Task AClaimTakenOverWhileTheResumeRuns_IsA409Skip_NeverAFailure_AndRecordsNothing()
    {
        var seeded = await SeedPausedReturnAsync();
        await EnqueueTimeoutAsync(seeded.Approval);
        var (processor, mode) = NewProcessor(new StubMode
        {
            OnResume = async () =>
            {
                await using var conn = await _pool.OpenAsync();
                await conn.ExecuteAsync("UPDATE approvals SET claimed_by = 'thief', claimed_at = NOW() WHERE id = @id", new { id = seeded.Approval.Id });
            },
        });

        var tick = await processor.RunOnceAsync("w1");

        tick.Failed.Should().Be(0);
        tick.Done.Should().Be(1);
        mode.Calls.Should().Be(1);
        var after = (await _repo.GetAsync(seeded.Approval.Id))!;
        after.Status.Should().Be(S.PendingReview, "the thief owns the decision now");
        after.ClaimedBy.Should().Be("thief");
    }

    [Fact]
    public async Task ARunAnApproverAlreadyResumed_IsRecordedNotRunAgain()
    {
        var seeded = await SeedPausedReturnAsync();
        await EnqueueTimeoutAsync(seeded.Approval);
        await using (var conn = await _pool.OpenAsync())
            await conn.ExecuteAsync(
                "UPDATE hitl_requests SET status = 'approved', response = @r::jsonb WHERE id = @id",
                new { id = seeded.HitlId, r = JsonSerializer.Serialize(new { approved = true, decided_by = Reviewer }) });
        var (processor, mode) = NewProcessor();

        await processor.RunOnceAsync("w1");

        mode.Calls.Should().Be(0, "the action already ran: it is recorded, not re-run");
        var after = (await _repo.GetAsync(seeded.Approval.Id))!;
        after.Status.Should().Be(S.Executed);
        after.DecidedBy.Should().Be(Reviewer);
    }

    [Theory]
    [InlineData("approved")]
    [InlineData("rejected")]
    [InlineData("expired")]
    public async Task AnApprovalDecidedSinceTheBreach_IsLeftAlone(string status)
    {
        var seeded = await SeedPausedReturnAsync();
        var rowId = await EnqueueTimeoutAsync(seeded.Approval);
        await using (var conn = await _pool.OpenAsync())
            await conn.ExecuteAsync("UPDATE approvals SET status = @status WHERE id = @id", new { id = seeded.Approval.Id, status });
        var (processor, mode) = NewProcessor();

        var tick = await processor.RunOnceAsync("w1");

        tick.Done.Should().Be(1);
        mode.Calls.Should().Be(0);
        (await _repo.GetAsync(seeded.Approval.Id))!.Status.Should().Be(status);
        ((object?)(await OutboxRowAsync(rowId)).processed_at).Should().NotBeNull();
    }

    [Fact]
    public async Task ATimeoutFromAnEarlierEscalationLevel_IsStale()
    {
        var seeded = await SeedPausedReturnAsync();
        await EnqueueTimeoutAsync(seeded.Approval, level: seeded.Approval.EscalationLevel + 1);
        var (processor, mode) = NewProcessor();

        (await processor.RunOnceAsync("w1")).Done.Should().Be(1);
        mode.Calls.Should().Be(0);
        (await _repo.GetAsync(seeded.Approval.Id))!.Status.Should().Be(S.PendingReview);
    }

    [Fact]
    public async Task TwoWorkersOnOneRow_ResumeTheWorkflowOnce()
    {
        var seeded = await SeedPausedReturnAsync();
        await EnqueueTimeoutAsync(seeded.Approval);
        var mode = new StubMode { Delay = TimeSpan.FromMilliseconds(200) };
        var (a, _) = NewProcessor(mode);
        var (b, _) = NewProcessor(mode);

        var ticks = await Task.WhenAll(a.RunOnceAsync("w1"), b.RunOnceAsync("w2"));

        ticks.Sum(t => t.Claimed).Should().Be(1, "FOR UPDATE SKIP LOCKED hands the row to exactly one worker");
        mode.Calls.Should().Be(1);
    }

    // ───────────────────────────── retry and parking ─────────────────────────────

    [Fact]
    public async Task AResumeThatFails_IsRetriedWithBackoff_ThenParked_AndTheApprovalStaysPending()
    {
        var seeded = await SeedPausedReturnAsync();
        var rowId = await EnqueueTimeoutAsync(seeded.Approval);
        var (processor, mode) = NewProcessor(new StubMode { FailWith = new InvalidOperationException("secret card 4111") });

        var first = await processor.RunOnceAsync("w1");
        first.Should().Be(new TimeoutTick(1, 0, 1, 0, 0));
        var row = await OutboxRowAsync(rowId);
        ((string)row.last_error).Should().Be("handler_error", "a fixed class, never the exception text");
        ((object?)row.failed_at).Should().BeNull();
        ((object?)row.locked_by).Should().BeNull();

        (await processor.RunOnceAsync("w1")).Failed.Should().Be(1);
        var last = await processor.RunOnceAsync("w1");
        last.Parked.Should().Be(1);

        row = await OutboxRowAsync(rowId);
        ((object?)row.failed_at).Should().NotBeNull();
        ((object?)row.processed_at).Should().BeNull();
        ((int)row.attempts).Should().Be(3);
        (await processor.RunOnceAsync("w1")).Claimed.Should().Be(0, "a parked row is neither retried nor processed again");
        mode.Calls.Should().Be(3);

        var approval = (await _repo.GetAsync(seeded.Approval.Id))!;
        approval.Status.Should().Be(S.PendingReview);
        approval.ClaimedBy.Should().BeNull("every failed attempt hands its claim back");
        (await HitlStatusAsync(seeded.HitlId)).Should().Be("pending");
    }

    [Fact]
    public void Backoff_Doubles_AndIsCapped() =>
        new[] { 1, 2, 3, 4, 5 }.Select(n => ApprovalTimeoutProcessor.BackoffSeconds(n, 5, 30)).Should().Equal(5, 10, 20, 30, 30);

    [Fact]
    public async Task ARowWhoseWorkerCrashedOnItsLastAttempt_IsParkedWhenItsLeaseRunsOut()
    {
        var seeded = await SeedPausedReturnAsync();
        var rowId = await EnqueueTimeoutAsync(seeded.Approval);
        await using (var conn = await _pool.OpenAsync())
            await conn.ExecuteAsync(
                "UPDATE outbox SET attempts = 3, locked_by = 'dead', locked_until = NOW() - interval '1 minute' WHERE id = @rowId", new { rowId });
        var (processor, _) = NewProcessor();

        var tick = await processor.RunOnceAsync("w1");

        tick.Parked.Should().Be(1);
        ((string)(await OutboxRowAsync(rowId)).last_error).Should().Be("lease_expired");
    }

    // ───────────────────────────── the hosted service ─────────────────────────────

    private async Task<bool> WorkerProcessesAsync(bool enabled)
    {
        var seeded = await SeedPausedReturnAsync();
        var rowId = await EnqueueTimeoutAsync(seeded.Approval);
        var settings = Settings(s => s with { DotnetTimeoutWorkerEnabled = enabled, OutboxPollIntervalSeconds = 0.1 });
        var (processor, _) = NewProcessor(settings: settings);
        var worker = new ApprovalTimeoutWorker(processor, settings, NullLogger<ApprovalTimeoutWorker>.Instance);

        await worker.StartAsync(CancellationToken.None);
        var deadline = DateTime.UtcNow.AddSeconds(enabled ? 10 : 1);
        bool processed = false;
        while (DateTime.UtcNow < deadline && !processed)
        {
            processed = (await OutboxRowAsync(rowId)).processed_at is not null;
            if (!processed) await Task.Delay(100);
        }
        await worker.StopAsync(CancellationToken.None);
        return processed;
    }

    [Fact]
    public async Task TheHostedService_DrainsDueTimeouts_WhenEnabled() => (await WorkerProcessesAsync(enabled: true)).Should().BeTrue();

    [Fact]
    public async Task TheHostedService_DoesNothing_WhenDisabled() => (await WorkerProcessesAsync(enabled: false)).Should().BeFalse();

    // ───────────────────────────── a stub for the resume itself ─────────────────────────────

    private sealed class StubMode : IOrchestrationMode, IResumableMode
    {
        private int _calls;
        public int Calls => Volatile.Read(ref _calls);
        public TimeSpan? Delay { get; init; }
        public Exception? FailWith { get; init; }
        public Func<Task>? OnResume { get; init; }
        public string? SeenEmail { get; private set; }
        public string? SeenRole { get; private set; }
        public ReturnDecision? LastDecision { get; private set; }

        public string Name => "workflow:return-replace";
        public string Label => "Return & Replace";
        public string Description => "test";
        public ModeCapabilities Capabilities => new(SupportsHitl: true);
        public string? GraphMermaid() => null;

        public Task<ModeRunResult> RunAsync(string message, RunContext ctx, CancellationToken ct = default) =>
            Task.FromResult(new ModeRunResult("ran", ["orchestrator"], 1));

        public Task<ModeRunResult> ResumeAsync(string sessionId, string checkpointId, string requestId, bool approved, CancellationToken ct = default) =>
            ResumeAsync(sessionId, checkpointId, requestId, ReturnDecision.FromApproved(approved), ct);

        public async Task<ModeRunResult> ResumeAsync(
            string sessionId, string checkpointId, string requestId, ReturnDecision decision, CancellationToken ct = default)
        {
            Interlocked.Increment(ref _calls);
            if (Delay is { } d) await Task.Delay(d, ct);
            if (FailWith is not null) throw FailWith;
            if (OnResume is { } hook) await hook();
            SeenEmail = RequestContext.CurrentUserEmail;
            SeenRole = RequestContext.CurrentUserRole;
            LastDecision = decision;
            return new ModeRunResult(decision.IsApproval ? "Return approved." : "Return rejected.", ["order-management"], 3);
        }
    }
}
