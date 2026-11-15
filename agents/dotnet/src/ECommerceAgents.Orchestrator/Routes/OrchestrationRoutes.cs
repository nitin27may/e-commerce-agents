using Microsoft.Extensions.Logging;
using System.Text.Json;
using ECommerceAgents.Shared.Data;
using Dapper;
using ECommerceAgents.Orchestrator.Modes;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Orchestration;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Routing;
using System.Diagnostics;

namespace ECommerceAgents.Orchestrator.Routes;

/// <summary>
/// <c>/api/orchestration/*</c> — the mode-introspection surface. Mirrors
/// Python's <c>orchestrator/routes/orchestration.py</c>.
/// </summary>
/// <remarks>
/// The whole prefix was absent from .NET, and every consumer failed silently:
/// the mode switcher returned <c>null</c> and vanished, the graph panel
/// rendered nothing, and the compare dialog opened with an empty list and a
/// button that could never activate. See #33.
///
/// <c>POST /{run_id}/resume</c> completes that set. It resumes a paused
/// <c>workflow:return-replace</c> run from its checkpoint, so a pending approval
/// survives the request that created it — and, because the checkpoint is durable,
/// an orchestrator restart too.
/// </remarks>
public static class OrchestrationRoutes
{
    private const int MaxCompareModes = 5;

    public static IEndpointRouteBuilder MapOrchestrationRoutes(this IEndpointRouteBuilder routes)
    {
        routes.MapGet("/api/orchestration/modes", ListModes);
        routes.MapGet("/api/orchestration/modes/{name}/graph", GetModeGraph);
        routes.MapPost("/api/orchestration/compare", CompareModes);
        routes.MapPost("/api/orchestration/{runId}/resume", ResumeRun);
        return routes;
    }

    /// <summary>Body of <c>POST /api/orchestration/{runId}/resume</c>.</summary>
    /// <remarks>
    /// <c>bool?</c>, not <c>bool</c>, on purpose. Snake-case binding would turn a missing
    /// or malformed body into <c>false</c> — silently *rejecting* a refund on a request
    /// that meant to approve one. Python's Pydantic model 422s on a missing field; this
    /// reconstructs that guard explicitly.
    /// </remarks>
    public sealed record ResumeRequest(bool? Approved);

    /// <summary>
    /// Resumes a paused workflow run after a human decision.
    /// </summary>
    /// <remarks>
    /// Mirrors Python's <c>resume_run</c> (<c>routes/orchestration.py:174</c>), including
    /// its error strings, so the message the UI surfaces is identical on both stacks.
    ///
    /// One deliberate difference: the pending row is claimed *before* the workflow runs,
    /// with the same guarded UPDATE <c>HitlRoutes</c> uses. Python updates it afterwards,
    /// which leaves a window where two clicks both resume, both finalize, and both
    /// release a refund — the loser only discovering it after the money moved. The claim
    /// is reverted if the resume throws, so a transient failure does not strand the row
    /// in <c>processing</c> with the button gone.
    /// </remarks>
    private static async Task<IResult> ResumeRun(
        string runId,
        ResumeRequest? body,
        DatabasePool pool,
        ModeRegistry registry,
        AgentSettings settings,
        ILoggerFactory loggers
    )
    {
        var email = RequestContext.CurrentUserEmail;
        if (string.IsNullOrWhiteSpace(email))
        {
            return Results.Unauthorized();
        }

        if (!Guid.TryParse(runId, out var runGuid))
        {
            // Python leans on asyncpg to coerce this; Dapper would surface it as a 500.
            return Results.NotFound(new { detail = "No pending approval found for this run" });
        }

        if (body?.Approved is not { } approved)
        {
            return Results.BadRequest(new { detail = "approved is required" });
        }

        // Admin only: whoever asked for the return must not be the one who approves it (#92).
        if (!string.Equals(RequestContext.CurrentUserRole, "admin", StringComparison.OrdinalIgnoreCase))
        {
            return Results.Json(new { detail = "Admin access required" }, statusCode: 403);
        }

        await using var conn = await pool.OpenAsync();

        var claimed = await conn.QueryFirstOrDefaultAsync(
            // Claimable = pending, or 'processing' with a claim older than the TTL (a resume
            // whose process died before releasing it; claimed_at is NULL for rows claimed
            // before migration 0002, which are equally stale). The predicate is repeated on
            // the outer UPDATE so two takeovers racing for one stale row cannot both win.
            @"UPDATE hitl_requests SET status = 'processing', claimed_at = NOW()
               WHERE id = (
                     SELECT id FROM hitl_requests
                      WHERE workflow_run_id = @runGuid
                        AND (status = 'pending' OR (status = 'processing'
                              AND (claimed_at IS NULL OR claimed_at < NOW() - make_interval(secs => @ttl))))
                        AND lower(user_email) <> lower(@email)
                      ORDER BY created_at DESC LIMIT 1)
                 AND (status = 'pending' OR (status = 'processing'
                      AND (claimed_at IS NULL OR claimed_at < NOW() - make_interval(secs => @ttl))))
           RETURNING id, kind, request_id, checkpoint_id, payload, user_email",
            new { runGuid, email, ttl = (double)settings.HitlClaimTtlSeconds });

        if (claimed is null)
        {
            // Nothing claimable. Distinguish "never existed" from "someone already took
            // it" (a double-click, which deserves to say so) and from "this is your own
            // request" (#92: an admin may not approve a return they raised).
            var latest = await conn.QueryFirstOrDefaultAsync(
                @"SELECT status, user_email FROM hitl_requests
                   WHERE workflow_run_id = @runGuid
                   ORDER BY created_at DESC LIMIT 1",
                new { runGuid });

            if (latest is null)
            {
                return Results.NotFound(new { detail = "No pending approval found for this run" });
            }

            if ((string)latest.status == "pending"
                && string.Equals((string)latest.user_email, email, StringComparison.OrdinalIgnoreCase))
            {
                return Results.Json(new { detail = "You cannot approve your own request" }, statusCode: 403);
            }

            return Results.Conflict(new { detail = $"Request is already {(string)latest.status}" });
        }

        var claimId = (Guid)claimed.id;
        var kind = (string)claimed.kind;
        var requestId = (string?)claimed.request_id;
        var checkpointId = (Guid?)claimed.checkpoint_id;

        try
        {
            if (kind != "return_approval")
            {
                return await ReleaseAsync(conn, claimId,
                    Results.BadRequest(new { detail = $"Resume not supported for request kind '{kind}'" }));
            }

            if (requestId is null || checkpointId is null)
            {
                // Where the seeded demo rows land: they carry neither, by design.
                return await ReleaseAsync(conn, claimId,
                    Results.Conflict(new { detail = "This pending request predates checkpoint-based resume" }));
            }

            if (registry.Get("workflow:return-replace") is not IResumableMode mode)
            {
                return await ReleaseAsync(conn, claimId,
                    Results.Conflict(new { detail = "This backend cannot resume workflow runs" }));
            }

            // Explicitly typed: `claimed` is Dapper's dynamic, so letting sessionId infer
            // from it makes every downstream call dynamically dispatched — including
            // mode.ResumeAsync, whose result then comes back as object and fails at
            // runtime on .AgentsInvolved rather than at compile time.
            string? sessionId = SessionIdFrom((object?)claimed.payload);
            if (sessionId is null)
            {
                return await ReleaseAsync(conn, claimId,
                    Results.Conflict(new { detail = "This pending request predates checkpoint-based resume" }));
            }

            // The rest of the workflow (loyalty discount, finalize) reads the caller's
            // identity from RequestContext, which is currently the approving admin. Run it
            // as the customer who asked for the return so their tier is applied, and put
            // the reviewer back afterwards. The reviewer is recorded as decided_by below.
            string requesterEmail = (string)claimed.user_email;
            string requesterRole = await conn.ExecuteScalarAsync<string?>(
                "SELECT role FROM users WHERE email = @requesterEmail", new { requesterEmail }) ?? "customer";
            var reviewerEmail = RequestContext.CurrentUserEmail;
            var reviewerRole = RequestContext.CurrentUserRole;
            ModeRunResult result;
            RequestContext.CurrentUserEmail = requesterEmail;
            RequestContext.CurrentUserRole = requesterRole;
            try
            {
                result = await mode.ResumeAsync(sessionId, checkpointId.Value.ToString(), requestId, approved);
            }
            finally
            {
                RequestContext.CurrentUserEmail = reviewerEmail;
                RequestContext.CurrentUserRole = reviewerRole;
            }

            await conn.ExecuteAsync(
                @"UPDATE hitl_requests
                     SET status = @status, responded_at = NOW(), response = @response::jsonb
                   WHERE id = @claimId AND status = 'processing'",
                new
                {
                    claimId,
                    status = approved ? "approved" : "rejected",
                    response = JsonSerializer.Serialize(new { approved, decided_by = reviewerEmail }),
                });

            // Back-link whatever the resumed run checkpointed, so /runs keeps showing the
            // full trail rather than stopping at the pause.
            string? newCheckpoint = result.LatestCheckpointId;
            if (newCheckpoint is not null && Guid.TryParse(newCheckpoint, out var newCid))
            {
                await conn.ExecuteAsync(
                    "UPDATE workflow_checkpoints SET usage_log_id = @runGuid WHERE checkpoint_id = @newCid",
                    new { runGuid, newCid });
            }

            return Results.Ok(new ResumeResponse(runId, approved, result.Text, result.AgentsInvolved.ToList()));
        }
        catch (Exception ex)
        {
            loggers.CreateLogger("hitl").LogError(ex, "hitl.resume_failed run={RunId}", runId);
            await conn.ExecuteAsync(
                "UPDATE hitl_requests SET status = 'pending', claimed_at = NULL WHERE id = @claimId AND status = 'processing'",
                new { claimId });
            return Results.Problem("Resume failed; the approval is still pending.");
        }
    }

    public sealed record ResumeResponse(string RunId, bool Approved, string Text, List<string> AgentsInvolved);

    /// <summary>Hands the claim back before returning a refusal, so the button stays live.</summary>
    private static async Task<IResult> ReleaseAsync(System.Data.Common.DbConnection conn, Guid claimId, IResult result)
    {
        await conn.ExecuteAsync(
            "UPDATE hitl_requests SET status = 'pending', claimed_at = NULL WHERE id = @claimId AND status = 'processing'",
            new { claimId });
        return result;
    }

    private static string? SessionIdFrom(object? payload)
    {
        if (payload is not string raw || string.IsNullOrWhiteSpace(raw))
        {
            return null;
        }
        try
        {
            return JsonDocument.Parse(raw).RootElement.TryGetProperty("session_id", out var v)
                ? v.GetString()
                : null;
        }
        catch (JsonException)
        {
            return null;
        }
    }

    private static IResult ListModes(ModeRegistry registry) => Results.Ok(registry.Describe());

    private static IResult GetModeGraph(string name, ModeRegistry registry)
    {
        try
        {
            var mode = registry.Get(name);
            // null is a legitimate answer, not an error: the tool router has no
            // fixed topology to draw, and the client renders nothing for it.
            return Results.Ok(new { name = mode.Name, mermaid = mode.GraphMermaid() });
        }
        catch (UnknownModeException ex)
        {
            return Results.NotFound(new { detail = ex.Message });
        }
    }

    public sealed record CompareRequest(string Message, List<string> Modes);

    /// <summary>
    /// Runs one prompt through several modes and reports each result.
    /// </summary>
    /// <remarks>
    /// Standalone — no conversation, no persisted history — so this compares
    /// modes on one prompt in isolation rather than as a turn in an ongoing
    /// chat.
    ///
    /// Sequential on purpose. The modes share one Postgres pool and the same
    /// specialist services, so running them concurrently would have them
    /// contend for the same resources and produce muddier latency numbers, not
    /// faster or more meaningful ones. A mode that throws is reported with its
    /// own error rather than aborting the comparison, so one broken mode can't
    /// hide the others' results.
    /// </remarks>
    private static async Task<IResult> CompareModes(
        [FromBody] CompareRequest? body,
        ModeRegistry registry,
        CancellationToken ct
    )
    {
        if (string.IsNullOrWhiteSpace(RequestContext.CurrentUserEmail))
        {
            return Results.Unauthorized();
        }

        if (body is null || string.IsNullOrWhiteSpace(body.Message))
        {
            return Results.BadRequest(new { detail = "message is required" });
        }

        var requested = (body.Modes ?? []).Distinct(StringComparer.OrdinalIgnoreCase).ToList();
        if (requested.Count < 2)
        {
            return Results.BadRequest(new { detail = "pick at least 2 modes to compare" });
        }
        if (requested.Count > MaxCompareModes)
        {
            return Results.BadRequest(new { detail = $"at most {MaxCompareModes} modes can be compared at once" });
        }

        var results = new List<object>();

        foreach (var name in requested)
        {
            IOrchestrationMode mode;
            try
            {
                mode = registry.Get(name);
            }
            catch (UnknownModeException ex)
            {
                results.Add(new
                {
                    mode = name,
                    label = name,
                    text = "",
                    latency_ms = 0,
                    agents_involved = Array.Empty<string>(),
                    step_count = 0,
                    graph_mermaid = (string?)null,
                    error = ex.Message,
                });
                continue;
            }

            var sw = Stopwatch.StartNew();
            try
            {
                var result = await mode.RunAsync(body.Message, new RunContext([], null), ct);
                sw.Stop();
                results.Add(new
                {
                    mode = mode.Name,
                    label = mode.Label,
                    text = result.Text,
                    latency_ms = (int)sw.ElapsedMilliseconds,
                    agents_involved = result.AgentsInvolved,
                    step_count = result.StepCount,
                    graph_mermaid = mode.GraphMermaid(),
                    error = (string?)null,
                });
            }
            catch (Exception ex)
            {
                sw.Stop();
                results.Add(new
                {
                    mode = mode.Name,
                    label = mode.Label,
                    text = "",
                    latency_ms = (int)sw.ElapsedMilliseconds,
                    agents_involved = Array.Empty<string>(),
                    step_count = 0,
                    graph_mermaid = mode.GraphMermaid(),
                    error = ex.Message,
                });
            }
        }

        return Results.Ok(new { message = body.Message, results });
    }
}
