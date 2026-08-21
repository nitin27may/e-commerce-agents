using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;

namespace ECommerceAgents.Shared.Guardrails;

/// <summary>
/// Tool-level role enforcement — the .NET counterpart of Python's
/// <c>shared/guardrails/roles.py::requires_role</c>.
/// </summary>
/// <remarks>
/// Python wraps a <c>@tool</c> function with a decorator; this is a
/// guard-clause helper instead — called as the first line of a guarded tool
/// method, mirroring the same source file's own <c>ensure_role</c> retrofit
/// style (used where a decorator can't wrap the call site). MAF .NET 1.18+
/// does have a function-invocation interceptor seam now (see
/// <see cref="Agents.SpecialistPipeline"/>'s use of
/// <c>FunctionInvocationDelegatingAgentBuilderExtensions.Use</c> for
/// <c>ToolAuditMiddleware</c>), but role checks stay call-site for a
/// different reason than this comment used to give: every guarded tool
/// returns its own typed result record built through a private
/// <c>Failure(string)</c> factory, and a single interceptor has no way to
/// construct the right denial shape for an arbitrary tool without weakening
/// those return types. (The earlier claim — that role requirements vary by
/// argument — is not borne out: all five call sites use static role lists.)
/// Because a guard clause is opt-in and easy to forget, the policy is
/// enforced instead by <c>DestructiveToolRoleGatingTests</c>, which fails the
/// build if a mutating tool ships without one. See issue #32.
/// Reads <see cref="RequestContext.CurrentUserRole"/> (the .NET analog of
/// Python's <c>current_user_role</c> ContextVar) rather than accepting the
/// role as a parameter, for the same "identity via ambient context, not
/// threaded arguments" reason.
/// </remarks>
public static class RoleGuard
{
    private const string AlwaysAllowedRole = "admin";

    /// <summary>
    /// Returns <see langword="null"/> when the current user's role is one of
    /// <paramref name="roles"/> (or "admin", always allowed) or when
    /// <see cref="AgentSettings.GuardrailsEnabled"/> is off. Otherwise
    /// returns a human-readable denial message — designed to be handed
    /// straight to a tool result's existing <c>Failure(string)</c> factory.
    /// </summary>
    public static string? Ensure(AgentSettings settings, params string[] roles)
    {
        if (!settings.GuardrailsEnabled)
        {
            return null;
        }

        var allowed = new HashSet<string>(roles, StringComparer.OrdinalIgnoreCase) { AlwaysAllowedRole };
        var role = RequestContext.CurrentUserRole;
        if (allowed.Contains(role))
        {
            return null;
        }

        var sortedRoles = string.Join(", ", allowed.OrderBy(r => r, StringComparer.Ordinal));
        return $"You don't have permission to perform this action. It requires one of these roles: {sortedRoles}.";
    }
}
