using ECommerceAgents.Shared.Logging;
using Microsoft.Extensions.Logging;
using System.Text.Json;
using ECommerceAgents.Shared.Data;
using Dapper;
using ECommerceAgents.Orchestrator.Approvals;
using ECommerceAgents.Orchestrator.Modes;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Cost;
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
        routes.MapPost("/api/orchestration/compare", CompareModes).AddEndpointFilter<ChatRoutes.ChatRateLimitFilter>();
        routes.MapPost("/api/orchestration/{runId}/resume", ResumeRun).AddEndpointFilter<ChatRoutes.ChatRateLimitFilter>();
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
    /// Resumes a paused workflow run after a human decision — a thin shim over the unified
    /// <see cref="ApprovalsService.DecideAsync"/>, exactly as Python's <c>resume_run</c> is.
    /// </summary>
    /// <remarks>
    /// Staff only (support or admin, at or above the level the approval policy assigned): the person who asked for a return must not be the
    /// one who approves it (#92), so the requester is refused with 403 before anything is claimed. Finds the latest <c>hitl_requests</c> row
    /// for the run (creating its inbox approval if it predates the inbox) and hands the decision to the service, which owns the claim /
    /// fence / resume machinery: a claim before the workflow runs (a second click is a 409), a <c>claimed_at</c> fencing token and TTL
    /// takeover for a crashed resume, the remaining steps executed as the requester with the reviewer recorded separately, and the claim
    /// handed back if the resume fails or is cancelled. A request with no <c>request_id</c>/<c>checkpoint_id</c> predates checkpoint wiring
    /// and is a 409. Error strings match Python's, so the message the UI surfaces is identical on both stacks.
    /// </remarks>
    private static async Task<IResult> ResumeRun(
        string runId,
        ResumeRequest? body,
        DatabasePool pool,
        ApprovalsService service
    )
    {
        if (StaffGuard.Require() is { } denied)
        {
            return denied;
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

        Guid? hitlId;
        await using (var conn = await pool.OpenAsync())
        {
            hitlId = await conn.ExecuteScalarAsync<Guid?>(
                "SELECT id FROM hitl_requests WHERE workflow_run_id = @runGuid ORDER BY created_at DESC LIMIT 1",
                new { runGuid });
        }
        var approvalId = hitlId is { } id ? await service.EnsureForHitlRequestAsync(id) : null;
        if (approvalId is null)
        {
            return Results.NotFound(new { detail = "No pending approval found for this run" });
        }

        var actor = new Actor(RequestContext.CurrentUserEmail, RequestContext.CurrentUserRole);
        try
        {
            var outcome = await service.DecideAsync(approvalId.Value.ToString(), actor, approved ? "approve" : "reject");
            var text = outcome.Result.TryGetProperty("text", out var t) ? t.GetString() ?? "" : "";
            var agents = outcome.Result.TryGetProperty("agents_involved", out var a) && a.ValueKind == JsonValueKind.Array
                ? a.EnumerateArray().Select(x => x.GetString() ?? "").ToList()
                : [];
            return Results.Ok(new ResumeResponse(runId, approved, text, agents));
        }
        catch (ApprovalHttpException ex)
        {
            return Results.Json(new { detail = ex.Detail }, statusCode: ex.StatusCode);
        }
    }

    public sealed record ResumeResponse(string RunId, bool Approved, string Text, List<string> AgentsInvolved);

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
                // A fresh scope per mode: its own usage accumulator (so one mode's tokens
                // are not added to the next), same caller identity.
                using var scope = RequestContext.Scope(
                    RequestContext.CurrentUserEmail,
                    RequestContext.CurrentUserRole,
                    RequestContext.CurrentSessionId);
                var result = await mode.RunAsync(body.Message, new RunContext([], null), ct);
                sw.Stop();

                // null = the mode reported no usage (not captured); 0 = it made no LLM call.
                long? tokensIn = null, tokensOut = null;
                double? estCost = null;
                if (result.NoLlmCalls)
                {
                    (tokensIn, tokensOut, estCost) = (0, 0, 0.0);
                }
                else if (RequestContext.CurrentRunUsage.Total() is { } total)
                {
                    tokensIn = total.InputTokenCount;
                    tokensOut = total.OutputTokenCount;
                    estCost = CostEstimator.EstimateRun(RequestContext.CurrentRunUsage.ByAgent);
                }

                results.Add(new
                {
                    mode = mode.Name,
                    label = mode.Label,
                    text = result.Text,
                    latency_ms = (int)sw.ElapsedMilliseconds,
                    agents_involved = result.AgentsInvolved,
                    step_count = result.StepCount,
                    graph_mermaid = mode.GraphMermaid(),
                    tokens_in = tokensIn,
                    tokens_out = tokensOut,
                    est_cost_usd = estCost,
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
