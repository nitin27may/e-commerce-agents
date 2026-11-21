using Dapper;
using ECommerceAgents.Shared.Approvals;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Logging;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using System.Text.Json;
using S = ECommerceAgents.Shared.Approvals.ApprovalStatus;

namespace ECommerceAgents.Orchestrator.Approvals;

/// <summary>What one <see cref="ApprovalTimeoutProcessor.RunOnceAsync"/> tick did.</summary>
public sealed record TimeoutTick(int Claimed, int Done, int Failed, int Parked, int Skipped);

/// <summary>
/// The .NET consumer of <c>approval.timeout_apply</c> outbox rows (parity row 36). Python's worker applies a top-level SLA breach by
/// resuming the paused return as a rejection, but it can only resume a run whose checkpoint Python's storage wrote; a run paused in the
/// .NET <c>workflow:return-replace</c> lives in MAF .NET's JSON checkpoint store. So the two stacks share the topic and split it by the
/// stack tag the creator wrote into <c>approvals.recommendation</c> (<see cref="ApprovalIntake.StackKey"/>): this class claims ONLY
/// workflow approvals tagged <c>dotnet</c>, and Python's claim query leaves exactly those rows alone.
/// </summary>
/// <remarks>
/// The lease shape is Python's <c>worker/runner.py</c>: rows are claimed with <c>FOR UPDATE SKIP LOCKED</c> and leased
/// (<c>locked_until</c> + <c>locked_by</c>), claiming counts the attempt, a failure records a fixed error class (never exception text),
/// backs off exponentially up to a cap and is parked (<c>failed_at</c>) after <c>OUTBOX_MAX_ATTEMPTS</c>. Each row is handled in its
/// own transaction that first re-checks it still owns the lease. Logs carry ids and fixed labels only.
/// </remarks>
public sealed class ApprovalTimeoutProcessor(
    DatabasePool pool,
    ApprovalRepository repo,
    ApprovalsService service,
    AgentSettings settings,
    ILogger<ApprovalTimeoutProcessor> logger)
{
    public const string Topic = "approval.timeout_apply";

    private sealed record Row(long Id, Guid? AggregateId, JsonElement Payload, int Attempts);

    /// <summary>Delay before the next try after <paramref name="attempts"/> tries: <c>base * 2^(attempts-1)</c>, never above <paramref name="cap"/>.</summary>
    public static double BackoffSeconds(int attempts, double baseSeconds, double cap) =>
        Math.Min(cap, baseSeconds * Math.Pow(2, Math.Max(attempts - 1, 0)));

    /// <summary>One tick: park crash-exhausted rows, claim a batch of due .NET timeouts, handle each in its own transaction.</summary>
    public async Task<TimeoutTick> RunOnceAsync(string workerId, CancellationToken ct = default)
    {
        var parked = await ParkExhaustedAsync();
        var rows = await ClaimAsync(workerId);
        int done = 0, failed = 0, skipped = 0;
        foreach (var row in rows)
        {
            if (ct.IsCancellationRequested)
            {
                await ReleaseAsync(row, workerId);
                continue;
            }
            switch (await ProcessAsync(row, workerId))
            {
                case "done": done++; break;
                case "skipped": skipped++; break;
                case "parked": parked++; break;
                default: failed++; break;
            }
        }
        return new TimeoutTick(rows.Count, done, failed, parked, skipped);
    }

    // The tag filter is the whole routing rule: only a workflow approval a .NET orchestrator opened.
    private const string OwnedRows =
        @"o.topic = 'approval.timeout_apply'
          AND EXISTS (SELECT 1 FROM approvals a
                       WHERE a.id = o.aggregate_id AND a.source = 'workflow' AND a.recommendation->>'stack' = 'dotnet')";

    private async Task<List<Row>> ClaimAsync(string workerId)
    {
        await using var conn = await pool.OpenAsync();
        var rows = await conn.QueryAsync(
            $@"UPDATE outbox SET locked_until = NOW() + make_interval(secs => @lease), locked_by = @workerId,
                                 attempts = attempts + 1
                WHERE id IN (SELECT o.id FROM outbox o
                              WHERE {OwnedRows}
                                AND o.processed_at IS NULL AND o.failed_at IS NULL
                                AND o.available_at <= NOW() AND o.attempts < @maxAttempts
                                AND (o.locked_until IS NULL OR o.locked_until < NOW())
                              ORDER BY o.available_at, o.id
                              LIMIT @batch
                                FOR UPDATE OF o SKIP LOCKED)
            RETURNING id, aggregate_id, payload::text AS payload, attempts",
            new { lease = (double)settings.OutboxLeaseSeconds, workerId, maxAttempts = settings.OutboxMaxAttempts, batch = settings.OutboxBatchSize });
        return rows
            .Select(r => new Row((long)r.id, (Guid?)r.aggregate_id, JsonDocument.Parse((string)r.payload).RootElement.Clone(), (int)r.attempts))
            .OrderBy(r => r.Id)
            .ToList();
    }

    /// <summary>Parks a row that used every attempt and whose last lease ran out without an outcome (a crashed worker).</summary>
    private async Task<int> ParkExhaustedAsync()
    {
        await using var conn = await pool.OpenAsync();
        return await conn.ExecuteAsync(
            $@"UPDATE outbox o SET failed_at = NOW(), last_error = 'lease_expired', locked_until = NULL, locked_by = NULL
                WHERE {OwnedRows}
                  AND o.processed_at IS NULL AND o.failed_at IS NULL AND o.attempts >= @maxAttempts
                  AND o.locked_until IS NOT NULL AND o.locked_until < NOW()",
            new { maxAttempts = settings.OutboxMaxAttempts });
    }

    /// <summary>Hands a claimed-but-unstarted row back (shutdown), un-counting the attempt.</summary>
    private async Task ReleaseAsync(Row row, string workerId)
    {
        await using var conn = await pool.OpenAsync();
        await conn.ExecuteAsync(
            @"UPDATE outbox SET locked_until = NULL, locked_by = NULL, attempts = GREATEST(attempts - 1, 0)
               WHERE id = @id AND locked_by = @workerId AND processed_at IS NULL",
            new { id = row.Id, workerId });
    }

    private async Task<string> ProcessAsync(Row row, string workerId)
    {
        try
        {
            await using var conn = await pool.OpenAsync();
            await using var tx = await conn.BeginTransactionAsync();
            var owned = await conn.ExecuteScalarAsync<int?>(
                @"SELECT 1 FROM outbox WHERE id = @id AND locked_by = @workerId
                     AND processed_at IS NULL AND failed_at IS NULL FOR UPDATE",
                new { id = row.Id, workerId }, tx);
            if (owned is null)
            {
                logger.LogInformation("outbox row skipped, lease lost id={Id}", row.Id);
                return "skipped";
            }
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(settings.OutboxLeaseSeconds));
            await HandleAsync(row).WaitAsync(timeout.Token);
            await conn.ExecuteAsync(
                @"UPDATE outbox SET processed_at = NOW(), locked_until = NULL, locked_by = NULL, last_error = NULL WHERE id = @id",
                new { id = row.Id }, tx);
            await tx.CommitAsync();
            return "done";
        }
        catch (Exception ex)
        {
            return await RecordFailureAsync(row, workerId, ex);
        }
    }

    private async Task<string> RecordFailureAsync(Row row, string workerId, Exception ex)
    {
        var errorClass = ex switch
        {
            TimeoutException or OperationCanceledException => "timeout",
            Npgsql.NpgsqlException => "database_error",
            _ => "handler_error",
        };
        await using var conn = await pool.OpenAsync();
        var parkedAt = await conn.ExecuteScalarAsync<bool?>(
            @"UPDATE outbox SET last_error = @errorClass, locked_until = NULL, locked_by = NULL,
                                available_at = NOW() + make_interval(secs => @delay),
                                failed_at = CASE WHEN attempts >= @maxAttempts THEN NOW() END
               WHERE id = @id AND locked_by = @workerId AND processed_at IS NULL
           RETURNING failed_at IS NOT NULL",
            new
            {
                errorClass,
                delay = BackoffSeconds(row.Attempts, settings.OutboxBackoffBaseSeconds, settings.OutboxBackoffMaxSeconds),
                maxAttempts = settings.OutboxMaxAttempts,
                id = row.Id,
                workerId,
            });
        logger.LogWarning(
            "outbox row failed id={Id} topic={Topic} class={Class} attempts={Attempts} parked={Parked}",
            row.Id, Topic, errorClass, row.Attempts, parkedAt == true);
        return parkedAt == true ? "parked" : "failed";
    }

    /// <summary>
    /// Applies one timeout, safe to run twice and late: an approval decided, answered or escalated since the breach is left alone. Anything
    /// that is not a conflict (a 5xx from the resume, a database error) is thrown so the row is retried and eventually parked.
    /// </summary>
    private async Task HandleAsync(Row row)
    {
        if (row.AggregateId is not { } approvalId
            || !row.Payload.TryGetProperty("approval_id", out var idElement) || idElement.GetString() is not { } payloadId
            || !Guid.TryParse(payloadId, out var payloadGuid) || payloadGuid != approvalId)
        {
            logger.LogInformation("approval timeout ignored, malformed row id={Id}", row.Id);
            return;
        }
        var current = await repo.GetAsync(approvalId);
        var level = row.Payload.TryGetProperty("level", out var l) && l.ValueKind == JsonValueKind.Number ? l.GetInt32() : -1;
        if (current is null
            || current.Status is not (S.PendingReview or S.NeedsInfo)
            || current.EscalationLevel != level)
        {
            logger.LogInformation("approval timeout ignored, no longer due id={Id}", approvalId);
            return;
        }
        var target = row.Payload.TryGetProperty("target", out var t) && t.GetString() == S.Rejected ? S.Rejected : S.Expired;
        try
        {
            await service.ExpireAsync(approvalId, "sla_timeout", target);
        }
        catch (ApprovalHttpException ex) when (ex.StatusCode < 500)
        {
            // A decide in flight, a lost claim, or an approval that moved on: not ours to finish, and not a failure.
            logger.LogInformation("approval timeout ignored, decided meanwhile id={Id} status={Status}", approvalId, ex.StatusCode);
            return;
        }
        await NotifyTimedOutAsync(current, target);
    }

    private async Task NotifyTimedOutAsync(Approval approval, string target)
    {
        await using var conn = await pool.OpenAsync();
        await conn.ExecuteAsync(
            @"INSERT INTO notifications (user_email, kind, title, body, link, approval_id)
              VALUES (@email, 'approval.timed_out', 'Your request was not reviewed in time', @body, '/orders', @approvalId)",
            new
            {
                email = approval.RequesterEmail.Trim().ToLowerInvariant(),
                body = $"Your {approval.Kind} request was closed as {target} because no one decided it in time.",
                approvalId = approval.Id,
            });
        logger.LogInformation("approval.timed_out id={Id} kind={Kind}", approval.Id, LogSafe.Clean(approval.Kind));
    }
}

/// <summary>
/// Runs <see cref="ApprovalTimeoutProcessor"/> on a poll loop inside the orchestrator process, unless
/// <c>DOTNET_TIMEOUT_WORKER_ENABLED=false</c>. A failing tick is logged and the loop carries on.
/// </summary>
public sealed class ApprovalTimeoutWorker(
    ApprovalTimeoutProcessor processor,
    AgentSettings settings,
    ILogger<ApprovalTimeoutWorker> logger) : BackgroundService
{
    private readonly string _workerId = $"dotnet-{Environment.ProcessId}-{Guid.NewGuid():N}";

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        if (!settings.DotnetTimeoutWorkerEnabled)
        {
            logger.LogInformation("approval timeout worker disabled (DOTNET_TIMEOUT_WORKER_ENABLED=false)");
            return;
        }
        var poll = TimeSpan.FromSeconds(Math.Max(settings.OutboxPollIntervalSeconds, 0.1));
        while (!stoppingToken.IsCancellationRequested)
        {
            var busy = false;
            try
            {
                var tick = await processor.RunOnceAsync(_workerId, stoppingToken);
                busy = tick.Claimed > 0;
                if (busy)
                    logger.LogInformation(
                        "approval timeout tick claimed={Claimed} done={Done} failed={Failed} parked={Parked} skipped={Skipped}",
                        tick.Claimed, tick.Done, tick.Failed, tick.Parked, tick.Skipped);
            }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
            {
                break;
            }
            catch (Exception ex)
            {
                logger.LogError(ex, "approval timeout tick failed");
            }
            if (busy) continue; // more may be due: do not sleep on a full batch
            try
            {
                await Task.Delay(poll, stoppingToken);
            }
            catch (OperationCanceledException)
            {
                break;
            }
        }
    }
}
