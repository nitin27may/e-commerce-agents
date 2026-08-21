using ECommerceAgents.Orchestrator.Modes;
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
/// <c>POST /{run_id}/resume</c> is not here — it drives
/// <c>ReturnAndReplaceWorkflow</c>, which has no runnable tool
/// implementation on .NET yet. It arrives with that workflow rather than
/// being stubbed into a route that would 500.
/// </remarks>
public static class OrchestrationRoutes
{
    private const int MaxCompareModes = 5;

    public static IEndpointRouteBuilder MapOrchestrationRoutes(this IEndpointRouteBuilder routes)
    {
        routes.MapGet("/api/orchestration/modes", ListModes);
        routes.MapGet("/api/orchestration/modes/{name}/graph", GetModeGraph);
        routes.MapPost("/api/orchestration/compare", CompareModes);
        return routes;
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
