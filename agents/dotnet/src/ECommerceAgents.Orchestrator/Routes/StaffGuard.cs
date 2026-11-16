using ECommerceAgents.Shared.Context;
using Microsoft.AspNetCore.Http;

namespace ECommerceAgents.Orchestrator.Routes;

/// <summary>
/// The .NET twin of Python's <c>require_staff</c> dependency
/// (<c>orchestrator/routes/legacy.py</c>): the caller must hold the
/// <c>support</c> or <c>admin</c> role. Sellers and customers are never staff.
/// Sits next to the per-file <c>RequireAdmin()</c> checks; it is not applied to
/// any route yet — who may approve moves to staff in a later step.
/// </summary>
public static class StaffGuard
{
    /// <summary>True for the roles that may review approvals.</summary>
    public static bool IsStaff(string? role) =>
        string.Equals(role, "support", StringComparison.OrdinalIgnoreCase)
        || string.Equals(role, "admin", StringComparison.OrdinalIgnoreCase);

    /// <summary>
    /// Null when the current request is staff; otherwise 401 (no identity) or 403.
    /// </summary>
    public static IResult? Require()
    {
        if (string.IsNullOrEmpty(RequestContext.CurrentUserEmail)) return Results.Unauthorized();
        if (!IsStaff(RequestContext.CurrentUserRole))
        {
            return Results.Json(new { detail = "Staff access required" }, statusCode: 403);
        }
        return null;
    }
}
