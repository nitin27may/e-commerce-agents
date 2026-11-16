using Dapper;
using ECommerceAgents.Orchestrator.Routes;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Orchestration;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;
using Microsoft.Extensions.DependencyInjection;
using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Xunit;

namespace ECommerceAgents.Orchestrator.Tests;

/// <summary>
/// <c>POST /api/orchestration/{runId}/resume</c> — the endpoint the Approve and Reject
/// buttons on <c>/runs</c> call.
/// </summary>
/// <remarks>
/// Until this route existed those buttons 404'd on the .NET stack, and the parity gate
/// did not notice: its checkpoint test asserts only that
/// <c>GET /api/runs/{id}/checkpoints</c> is served, deliberately, to avoid coupling
/// itself to the mode registry. Nothing clicked Approve.
/// </remarks>
[Collection(nameof(LocalPostgresCollection))]
public sealed class ResumeRouteTests : IAsyncLifetime
{
    private readonly PostgresFixture _pg;
    private DatabasePool _pool = null!;
    private const string Owner = "owner@example.com";
    private const string Stranger = "stranger@example.com";
    private const string Approver = "approver@example.com";

    public ResumeRouteTests(PostgresFixture pg) => _pg = pg;

    public async Task InitializeAsync()
    {
        _pool = new DatabasePool(new AgentSettings { DatabaseUrl = _pg.ConnectionString });
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            @"TRUNCATE hitl_requests, workflow_checkpoints, agent_execution_steps, usage_logs, users
              RESTART IDENTITY CASCADE");
        foreach (var email in new[] { Owner, Stranger })
        {
            await conn.ExecuteAsync(
                "INSERT INTO users (email, password_hash, name, role) VALUES (@email, 'x', 'T', 'customer')",
                new { email });
        }
    }

    public async Task DisposeAsync() => await _pool.DisposeAsync();

    // ─────────────────────── refusals ───────────────────────

    [Fact]
    public async Task AnAnonymousCaller_IsRefused()
    {
        using var client = ClientFor(new StubResumableMode(), authenticated: false);
        var res = await client.PostAsJsonAsync($"/api/orchestration/{Guid.NewGuid()}/resume", new { approved = true });
        res.StatusCode.Should().Be(HttpStatusCode.Unauthorized);
    }

    [Fact]
    public async Task ARunIdThatIsNotAGuid_Is404_NotACrash()
    {
        using var client = ClientFor(new StubResumableMode());
        var res = await client.PostAsJsonAsync("/api/orchestration/not-a-guid/resume", new { approved = true });
        res.StatusCode.Should().Be(HttpStatusCode.NotFound);
    }

    [Fact]
    public async Task AMissingApprovedField_Is400_RatherThanASilentRejection()
    {
        var (runId, _) = await SeedPendingAsync();
        using var client = ClientFor(new StubResumableMode());

        // The trap this guards: binding a missing field to a non-nullable bool would
        // default it to false and *reject* a refund the reviewer meant to approve.
        var res = await client.PostAsJsonAsync($"/api/orchestration/{runId}/resume", new { });

        res.StatusCode.Should().Be(HttpStatusCode.BadRequest);
        (await StatusOfAsync(runId)).Should().Be("pending", "a refused request must stay actionable");
    }

    /// <summary>
    /// #92 — the requester used to be allowed to resume their own run, i.e. to approve
    /// their own refund. Only an admin may now, and a refusal must not move the row.
    /// </summary>
    [Theory]
    [InlineData(Owner)]
    [InlineData(Stranger)]
    public async Task ACustomer_IsRefused_EvenForTheirOwnRun(string email)
    {
        var (runId, _) = await SeedPendingAsync();
        var mode = new StubResumableMode();
        using var client = ClientFor(mode, email: email, role: "customer");

        var res = await client.PostAsJsonAsync($"/api/orchestration/{runId}/resume", new { approved = true });

        res.StatusCode.Should().Be(HttpStatusCode.Forbidden);
        mode.Calls.Should().Be(0);
        (await StatusOfAsync(runId)).Should().Be("pending");
    }

    /// <summary>#92 — being an admin is not enough: nobody approves the return they raised.</summary>
    [Fact]
    public async Task AnAdmin_CannotApproveAReturnTheyRaised_CaseInsensitively()
    {
        var (runId, _) = await SeedPendingAsync(requester: Approver.ToUpperInvariant());
        var mode = new StubResumableMode();
        using var client = ClientFor(mode);

        var res = await client.PostAsJsonAsync($"/api/orchestration/{runId}/resume", new { approved = true });

        res.StatusCode.Should().Be(HttpStatusCode.Forbidden);
        mode.Calls.Should().Be(0);
        (await StatusOfAsync(runId)).Should().Be("pending");
    }

    [Fact]
    public async Task AnAdminResumingARunThatDoesNotExist_Is404()
    {
        using var client = ClientFor(new StubResumableMode());
        var res = await client.PostAsJsonAsync($"/api/orchestration/{Guid.NewGuid()}/resume", new { approved = true });
        res.StatusCode.Should().Be(HttpStatusCode.NotFound);
    }

    [Fact]
    public async Task ARequestWithNoCheckpoint_Is409_WhichIsWhereTheSeededDemoRowsLand()
    {
        var (runId, _) = await SeedPendingAsync(withCheckpoint: false);
        using var client = ClientFor(new StubResumableMode());

        var res = await client.PostAsJsonAsync($"/api/orchestration/{runId}/resume", new { approved = true });

        res.StatusCode.Should().Be(HttpStatusCode.Conflict);
        (await res.Content.ReadAsStringAsync()).Should().Contain("predates checkpoint-based resume");
        (await StatusOfAsync(runId)).Should().Be("pending", "the claim must be handed back on a refusal");
    }

    [Fact]
    public async Task AnUnsupportedKind_Is400_AndReleasesTheClaim()
    {
        var (runId, _) = await SeedPendingAsync(kind: "tool_approval");
        using var client = ClientFor(new StubResumableMode());

        var res = await client.PostAsJsonAsync($"/api/orchestration/{runId}/resume", new { approved = true });

        res.StatusCode.Should().Be(HttpStatusCode.BadRequest);
        (await StatusOfAsync(runId)).Should().Be("pending");
    }

    // ─────────────────────── the happy paths ────────────────

    [Fact]
    public async Task ApprovingResumesTheRunAndRecordsTheDecision()
    {
        var (runId, checkpointId) = await SeedPendingAsync();
        var mode = new StubResumableMode();
        using var client = ClientFor(mode);

        var res = await client.PostAsJsonAsync($"/api/orchestration/{runId}/resume", new { approved = true });
        res.EnsureSuccessStatusCode();

        var body = await res.Content.ReadFromJsonAsync<JsonElement>();
        body.GetProperty("approved").GetBoolean().Should().BeTrue();
        body.GetProperty("run_id").GetString().Should().Be(runId.ToString());
        body.GetProperty("text").GetString().Should().NotBeNullOrEmpty();

        mode.Calls.Should().Be(1);
        mode.LastApproved.Should().BeTrue();
        mode.LastCheckpointId.Should().Be(checkpointId.ToString());
        (await StatusOfAsync(runId)).Should().Be("approved");
    }

    /// <summary>
    /// #92 — the resumed workflow's loyalty step reads the caller from RequestContext,
    /// which is the approving admin. It must see the customer who asked for the return,
    /// and the reviewer must be recorded rather than lost.
    /// </summary>
    [Fact]
    public async Task TheResumedWorkflowRunsAsTheRequester_AndTheReviewerIsRecorded()
    {
        var (runId, _) = await SeedPendingAsync();
        var mode = new StubResumableMode();
        using var client = ClientFor(mode);

        (await client.PostAsJsonAsync($"/api/orchestration/{runId}/resume", new { approved = true }))
            .EnsureSuccessStatusCode();

        mode.SeenEmail.Should().Be(Owner);
        mode.SeenRole.Should().Be("customer");
        await using var conn = await _pool.OpenAsync();
        var response = await conn.ExecuteScalarAsync<string>(
            "SELECT response::text FROM hitl_requests WHERE workflow_run_id = @runId", new { runId });
        using var doc = JsonDocument.Parse(response!);
        doc.RootElement.GetProperty("decided_by").GetString().Should().Be(Approver);
        doc.RootElement.GetProperty("approved").GetBoolean().Should().BeTrue();
    }

    [Fact]
    public async Task RejectingIsRecordedAsRejected()
    {
        var (runId, _) = await SeedPendingAsync();
        var mode = new StubResumableMode();
        using var client = ClientFor(mode);

        var res = await client.PostAsJsonAsync($"/api/orchestration/{runId}/resume", new { approved = false });
        res.EnsureSuccessStatusCode();

        mode.LastApproved.Should().BeFalse();
        (await StatusOfAsync(runId)).Should().Be("rejected");
    }

    [Fact]
    public async Task ResumingTwice_IsRefusedTheSecondTime()
    {
        var (runId, _) = await SeedPendingAsync();
        var mode = new StubResumableMode();
        using var client = ClientFor(mode);

        (await client.PostAsJsonAsync($"/api/orchestration/{runId}/resume", new { approved = true }))
            .EnsureSuccessStatusCode();
        var second = await client.PostAsJsonAsync($"/api/orchestration/{runId}/resume", new { approved = true });

        second.StatusCode.Should().Be(HttpStatusCode.Conflict);
        mode.Calls.Should().Be(1);
    }

    /// <summary>
    /// The test the claim-before-execute pattern exists for.
    /// </summary>
    /// <remarks>
    /// Python claims the row *after* resuming, so two clicks can both resume, both
    /// finalize and both release a refund — the loser only finding out once the money has
    /// moved. Asserting the HTTP codes alone would not catch that; what matters is that
    /// the workflow ran exactly once.
    /// </remarks>
    [Fact]
    public async Task TwoConcurrentApprovals_ResumeTheWorkflowExactlyOnce()
    {
        var (runId, _) = await SeedPendingAsync();
        var mode = new StubResumableMode(delay: TimeSpan.FromMilliseconds(150));
        using var a = ClientFor(mode);
        using var b = ClientFor(mode);

        var results = await Task.WhenAll(
            a.PostAsJsonAsync($"/api/orchestration/{runId}/resume", new { approved = true }),
            b.PostAsJsonAsync($"/api/orchestration/{runId}/resume", new { approved = true }));

        results.Count(r => r.IsSuccessStatusCode).Should().Be(1, "only one click can spend a refund");
        results.Count(r => r.StatusCode == HttpStatusCode.Conflict).Should().Be(1);
        mode.Calls.Should().Be(1, "the refund side effect must run exactly once, not merely report once");
        (await StatusOfAsync(runId)).Should().Be("approved");
    }

    // ─────────────── stale-claim takeover (#102, migration 0002) ───────────────

    [Theory]
    [InlineData("NOW() - interval '1 hour'")]
    [InlineData("NULL")]
    public async Task AStaleProcessingClaim_IsTakenOver(string claimedAtSql)
    {
        var (runId, _) = await SeedPendingAsync();
        await ForceClaimAsync(runId, claimedAtSql);
        var mode = new StubResumableMode();
        using var client = ClientFor(mode);

        var res = await client.PostAsJsonAsync($"/api/orchestration/{runId}/resume", new { approved = true });

        res.EnsureSuccessStatusCode();
        mode.Calls.Should().Be(1);
        (await StatusOfAsync(runId)).Should().Be("approved");
    }

    [Fact]
    public async Task AFreshProcessingClaim_IsNotTakenOver()
    {
        var (runId, _) = await SeedPendingAsync();
        await ForceClaimAsync(runId, "NOW()");
        var mode = new StubResumableMode();
        using var client = ClientFor(mode);

        var res = await client.PostAsJsonAsync($"/api/orchestration/{runId}/resume", new { approved = true });

        res.StatusCode.Should().Be(HttpStatusCode.Conflict);
        mode.Calls.Should().Be(0);
        (await StatusOfAsync(runId)).Should().Be("processing");
    }

    [Fact]
    public async Task AClaimIsStampedWhileResuming_AndReleasingClearsIt()
    {
        // Released path: no request_id, so the claim is handed straight back.
        var (released, _) = await SeedPendingAsync(withCheckpoint: false);
        using (var client = ClientFor(new StubResumableMode()))
        {
            (await client.PostAsJsonAsync($"/api/orchestration/{released}/resume", new { approved = true }))
                .StatusCode.Should().Be(HttpStatusCode.Conflict);
        }
        (await StatusOfAsync(released)).Should().Be("pending");
        (await ClaimedAtAsync(released)).Should().BeNull("a released claim must not look held");

        // Held path: the stamp is visible while the workflow is running.
        var (held, _) = await SeedPendingAsync();
        DateTime? seenDuringResume = null;
        var mode = new StubResumableMode(delay: TimeSpan.FromMilliseconds(300));
        using var c2 = ClientFor(mode);
        var inFlight = c2.PostAsJsonAsync($"/api/orchestration/{held}/resume", new { approved = true });
        for (var i = 0; i < 40 && seenDuringResume is null; i++)
        {
            await Task.Delay(25);
            seenDuringResume = await ClaimedAtAsync(held);
        }
        (await inFlight).EnsureSuccessStatusCode();
        seenDuringResume.Should().NotBeNull("the claim must stamp claimed_at before the workflow runs");
    }

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public async Task ATakenOverClaim_CannotFinalizeOrRelease(bool firstFails)
    {
        var (runId, _) = await SeedPendingAsync();
        var gate = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var mode = new StubResumableMode(gateFirstCall: gate, failFirstCall: firstFails);

        // A claims and freezes.
        using var clientA = ClientFor(mode, email: "a@example.com");
        var aInFlight = clientA.PostAsJsonAsync($"/api/orchestration/{runId}/resume", new { approved = true });
        for (var i = 0; i < 80 && mode.Calls == 0; i++)
        {
            await Task.Delay(25);
        }
        mode.Calls.Should().Be(1);

        // A's claim ages past the TTL; B takes it over and completes.
        await ForceClaimAsync(runId, "NOW() - interval '1 hour'");
        using var clientB = ClientFor(mode, email: "b@example.com");
        var b = await clientB.PostAsJsonAsync($"/api/orchestration/{runId}/resume", new { approved = false });
        b.StatusCode.Should().Be(HttpStatusCode.OK);

        // A wakes: its finalize (or its failure-release) must be a no-op.
        gate.SetResult();
        var a = await aInFlight;
        if (!firstFails)
        {
            a.StatusCode.Should().Be(HttpStatusCode.Conflict, "A must be told its claim was lost");
        }

        (await StatusOfAsync(runId)).Should().Be("rejected", "B's decision stands");
        await using var conn = await _pool.OpenAsync();
        (await conn.ExecuteScalarAsync<string>(
            "SELECT response::text FROM hitl_requests WHERE workflow_run_id = @runId", new { runId }))
            .Should().Contain("b@example.com");
    }

    // ─────────────────────── helpers ────────────────────────

    private async Task ForceClaimAsync(Guid runId, string claimedAtSql)
    {
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            $"UPDATE hitl_requests SET status = 'processing', claimed_at = {claimedAtSql} WHERE workflow_run_id = @runId",
            new { runId });
        // A resume now holds two claims: the approval's (taken by the unified decide) and the hitl row's (taken when the workflow
        // resumes). A crashed resume leaves both stale, so age the approval's together with the hitl one — same as the Python helper.
        await conn.ExecuteAsync(
            $@"UPDATE approvals SET claimed_at = {claimedAtSql}
                WHERE source = 'workflow'
                  AND source_ref IN (SELECT id FROM hitl_requests WHERE workflow_run_id = @runId)",
            new { runId });
    }

    private async Task<DateTime?> ClaimedAtAsync(Guid runId)
    {
        await using var conn = await _pool.OpenAsync();
        return await conn.ExecuteScalarAsync<DateTime?>(
            "SELECT claimed_at FROM hitl_requests WHERE workflow_run_id = @runId", new { runId });
    }

    private async Task<(Guid RunId, Guid? CheckpointId)> SeedPendingAsync(
        bool withCheckpoint = true,
        string kind = "return_approval",
        string requester = Owner
    )
    {
        await using var conn = await _pool.OpenAsync();
        var userId = await conn.ExecuteScalarAsync<Guid>(
            "SELECT id FROM users WHERE email = @Owner", new { Owner });
        var runId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO usage_logs (user_id, agent_name, input_summary, status)
              VALUES (@userId, 'orchestrator', 'return', 'success') RETURNING id",
            new { userId });

        Guid? checkpointId = null;
        if (withCheckpoint)
        {
            checkpointId = Guid.NewGuid();
            await conn.ExecuteAsync(
                @"INSERT INTO workflow_checkpoints (checkpoint_id, workflow_name, payload, session_id, usage_log_id)
                  VALUES (@checkpointId, 'return-replace', '{}'::jsonb, 'sess-1', @runId)",
                new { checkpointId, runId });
        }

        await conn.ExecuteAsync(
            @"INSERT INTO hitl_requests
                  (workflow_run_id, request_id, checkpoint_id, user_email, kind, payload, status)
              VALUES (@runId, @requestId, @checkpointId, @requester, @kind, @payload::jsonb, 'pending')",
            new
            {
                runId,
                requestId = withCheckpoint ? "req-1" : null,
                checkpointId,
                requester,
                kind,
                payload = JsonSerializer.Serialize(new { session_id = "sess-1" }),
            });

        return (runId, checkpointId);
    }

    private async Task<string?> StatusOfAsync(Guid runId)
    {
        await using var conn = await _pool.OpenAsync();
        return await conn.ExecuteScalarAsync<string?>(
            "SELECT status FROM hitl_requests WHERE workflow_run_id = @runId", new { runId });
    }

    // Admin by default: since #92 only staff can resume, and the requester ("Owner")
    // must not be the one approving.
    private HttpClient ClientFor(
        IOrchestrationMode mode, bool authenticated = true, string email = Approver, string role = "admin")
    {
        var server = OrchestratorTestHost.Create(
            _pool,
            r => r.MapOrchestrationRoutes(),
            configureServices: services =>
            {
                // The host registers ToolRouterMode by default, which needs an agent even
                // though no test here routes a chat turn through it.
                services.AddSingleton<IChatClient>(new FakeChatClient().EnqueueResponse("unused"));
                services.AddSingleton<AIAgent>(sp =>
                    sp.GetRequiredService<IChatClient>().AsAIAgent(instructions: "t", name: "orchestrator"));
                services.AddSingleton(mode);
            });
        var client = server.CreateClient();
        if (authenticated)
        {
            client.DefaultRequestHeaders.Add("X-Test-Email", email);
            client.DefaultRequestHeaders.Add("X-Test-Role", role);
        }
        return client;
    }

    /// <summary>Counts how many times the workflow was actually driven.</summary>
    private sealed class StubResumableMode(
        TimeSpan? delay = null,
        TaskCompletionSource? gateFirstCall = null,
        bool failFirstCall = false
    ) : IOrchestrationMode, IResumableMode
    {
        private int _calls;

        public int Calls => Volatile.Read(ref _calls);
        public string? SeenEmail { get; private set; }
        public string? SeenRole { get; private set; }
        public bool? LastApproved { get; private set; }
        public string? LastCheckpointId { get; private set; }

        public string Name => "workflow:return-replace";
        public string Label => "Return & Replace";
        public string Description => "test";
        public ModeCapabilities Capabilities => new(SupportsHitl: true);
        public string? GraphMermaid() => null;

        public Task<ModeRunResult> RunAsync(string message, RunContext ctx, CancellationToken ct = default)
            => Task.FromResult(new ModeRunResult("ran", ["orchestrator"], 1));

        public async Task<ModeRunResult> ResumeAsync(
            string sessionId, string checkpointId, string requestId, bool approved, CancellationToken ct = default)
        {
            var callNumber = Interlocked.Increment(ref _calls);
            if (callNumber == 1 && gateFirstCall is not null)
            {
                await gateFirstCall.Task; // the first resume is frozen until the test lets it go
                if (failFirstCall)
                {
                    throw new InvalidOperationException("boom");
                }
            }
            SeenEmail = RequestContext.CurrentUserEmail;
            SeenRole = RequestContext.CurrentUserRole;
            LastApproved = approved;
            LastCheckpointId = checkpointId;
            if (delay is { } d)
            {
                // Widens the window a second caller could slip through, so the claim is
                // tested rather than the scheduler happening to serialise the two.
                await Task.Delay(d, ct);
            }
            return new ModeRunResult(approved ? "Return approved." : "Return rejected.", ["order-management"], 3);
        }
    }
}
