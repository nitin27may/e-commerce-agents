using ECommerceAgents.Orchestrator.Approvals;
using ECommerceAgents.Shared.Approvals;
using ECommerceAgents.Shared.Context;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Routing;
using System.Text.Json;

namespace ECommerceAgents.Orchestrator.Routes;

/// <summary>
/// The unified approvals API — the .NET twin of Python's <c>orchestrator/routes/approvals.py</c>, with the same paths, JSON field names,
/// status codes and <c>{"detail": ...}</c> error bodies, because the web UI calls one API for both stacks.
/// <list type="table">
/// <item><term><c>GET /api/approvals</c></term><description>staff: the queue for their role (open items by default)</description></item>
/// <item><term><c>GET /api/approvals/{id}</c></term><description>staff: one approval with its audit events and policy evidence</description></item>
/// <item><term><c>POST /api/approvals/{id}/decide</c></term><description>staff: approve (with optional edits) | reject | request_info</description></item>
/// <item><term><c>POST /api/approvals/{id}/info</c></term><description>the requester: answer a request for more information</description></item>
/// <item><term><c>GET /api/me/approvals</c></term><description>any signed-in user: their own approvals, status only</description></item>
/// </list>
/// Every rule lives in <see cref="ApprovalsService"/> (who may decide, exactly-once execution, the edits allowlist); this class only
/// parses the request and maps the service's refusals onto HTTP. Identity comes from the verified token (<see cref="RequestContext"/>),
/// never from the body.
/// </summary>
public static class ApprovalRoutes
{
    /// <summary>Body of <c>POST /api/approvals/{id}/decide</c>.</summary>
    public sealed record DecideBody(string? Decision = null, Dictionary<string, JsonElement>? Edits = null, string? Note = null);

    /// <summary>Body of <c>POST /api/approvals/{id}/info</c>.</summary>
    public sealed record InfoBody(string? Message = null);

    private static readonly string[] Open = [ApprovalStatus.PendingReview, ApprovalStatus.NeedsInfo];

    public static IEndpointRouteBuilder MapApprovalRoutes(this IEndpointRouteBuilder routes)
    {
        routes.MapGet("/api/approvals", ListApprovals);
        routes.MapGet("/api/approvals/{approvalId}", GetApproval);
        routes.MapPost("/api/approvals/{approvalId}/decide", Decide);
        routes.MapPost("/api/approvals/{approvalId}/info", ProvideInfo);
        routes.MapGet("/api/me/approvals", MyApprovals);
        return routes;
    }

    private static Actor ActorOfRequest() => new(RequestContext.CurrentUserEmail, RequestContext.CurrentUserRole);

    private static IResult Refusal(ApprovalHttpException ex) =>
        Results.Json(new { detail = ex.Detail }, statusCode: ex.StatusCode);

    private static object Summary(Approval a)
    {
        var full = a.ToJson();
        var keep = new[]
        {
            "id", "source", "kind", "requester_email", "amount", "route", "assigned_role", "status", "due_at",
            "escalation_level", "created_at", "updated_at",
        };
        return keep.ToDictionary(k => k, k => full[k]);
    }

    /// <summary>The reviewer queue. <c>status</c> is one status or <c>all</c>; omitted means what is waiting (review or info).</summary>
    private static async Task<IResult> ListApprovals(ApprovalsService service, string? status = null, int limit = 50)
    {
        if (StaffGuard.Require() is { } denied) return denied;

        IReadOnlyCollection<string>? statuses;
        if (status is null) statuses = Open;
        else if (status == "all") statuses = null;
        else if (ApprovalStatus.IsKnown(status)) statuses = [status];
        else return Results.Json(new { detail = "Unknown status" }, statusCode: 422);

        // Support sees (and may decide) what is routed to support; admin sees everything.
        var actor = ActorOfRequest();
        IReadOnlyCollection<string>? roles = ApprovalIntake.Rank(actor.Role) >= ApprovalIntake.Rank("admin") ? null : ["support"];
        var rows = await service.Repository.ListForStaffAsync(statuses, roles, limit, excludeSources: ["proposal"]);
        return Results.Ok(new { approvals = rows.Select(Summary).ToList(), total = rows.Count });
    }

    private static async Task<IResult> GetApproval(string approvalId, ApprovalsService service)
    {
        if (StaffGuard.Require() is { } denied) return denied;
        try
        {
            var approval = await service.LoadAsync(approvalId);
            // Left out of the staff list, so left out here: a proposal is the seller's to read (Python's seller routes).
            if (approval.Source == "proposal") throw new ApprovalHttpException(404, "Approval not found");
            if (!ApprovalsService.CanReview(approval, ActorOfRequest()))
                throw new ApprovalHttpException(403, "This approval is routed to a manager");
            return Results.Ok(await service.DetailAsync(approval));
        }
        catch (ApprovalHttpException ex)
        {
            return Refusal(ex);
        }
    }

    private static async Task<IResult> Decide(string approvalId, [FromBody] DecideBody? body, ApprovalsService service)
    {
        if (StaffGuard.Require() is { } denied) return denied;
        if (body?.Decision is not ("approve" or "reject" or "request_info"))
            return Results.Json(new { detail = "decision must be approve, reject or request_info" }, statusCode: 422);
        if (body.Note is { Length: > ApprovalsService.MaxNoteLength })
            return Results.Json(new { detail = $"note must be at most {ApprovalsService.MaxNoteLength} characters" }, statusCode: 422);
        try
        {
            var outcome = await service.DecideAsync(approvalId, ActorOfRequest(), body.Decision, body.Edits, body.Note);
            return Results.Ok(outcome.ToBody());
        }
        catch (ApprovalHttpException ex)
        {
            return Refusal(ex);
        }
    }

    /// <summary>The requester answers a request for more information; the approval returns to review.</summary>
    private static async Task<IResult> ProvideInfo(string approvalId, [FromBody] InfoBody? body, ApprovalsService service)
    {
        if (string.IsNullOrEmpty(RequestContext.CurrentUserEmail)) return Results.Unauthorized();
        if (string.IsNullOrEmpty(body?.Message) || body.Message.Length > ApprovalsService.MaxNoteLength)
            return Results.Json(
                new { detail = $"message must be 1 to {ApprovalsService.MaxNoteLength} characters" }, statusCode: 422);
        try
        {
            var outcome = await service.ProvideInfoAsync(approvalId, ActorOfRequest(), body.Message);
            return Results.Ok(outcome.ToBody());
        }
        catch (ApprovalHttpException ex)
        {
            return Refusal(ex);
        }
    }

    /// <summary>Status only: what the requester needs to know where their request stands, none of the reviewer's evidence.</summary>
    private static async Task<IResult> MyApprovals(ApprovalsService service, int limit = 50)
    {
        var email = RequestContext.CurrentUserEmail;
        if (string.IsNullOrEmpty(email)) return Results.Unauthorized();
        var rows = await service.Repository.ListForRequesterAsync(email, limit);
        var keep = new[] { "id", "kind", "status", "amount", "created_at", "updated_at" };
        return Results.Ok(new
        {
            approvals = rows.Select(a =>
            {
                var full = a.ToJson();
                return keep.ToDictionary(k => k, k => full[k]);
            }).ToList(),
        });
    }
}
