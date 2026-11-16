namespace ECommerceAgents.Shared.Guardrails;

/// <summary>
/// Declares, on a tool method, the roles allowed to call it — the metadata twin of Python's
/// <c>requires_role</c> decorator, which records the same list on the function it wraps.
/// </summary>
/// <remarks>
/// <para>
/// It is <b>metadata only</b>. Enforcement stays where it has always been: the
/// <see cref="RoleGuard.Ensure"/> call at the top of the method body (a guard clause rather than a
/// decorator, for the reasons in <see cref="RoleGuard"/>). The attribute exists so something other than the
/// method body can know the requirement — today the public A2A agent card, which must not advertise a
/// tool the least-privileged role (<c>customer</c>) could not call.
/// </para>
/// <para>
/// It does not change the tool's name, description or parameter schema (the function factory ignores it),
/// so what the model is shown is untouched. <c>A2ARoleMetadataTests</c> cross-checks every
/// <c>RoleGuard.Ensure</c> call in the source against the attribute on the same method, so the two cannot
/// drift. <c>admin</c> is always allowed by <see cref="RoleGuard"/> and need not be listed.
/// </para>
/// </remarks>
[AttributeUsage(AttributeTargets.Method, AllowMultiple = false, Inherited = false)]
public sealed class RequiresRoleAttribute(params string[] roles) : Attribute
{
    public IReadOnlyList<string> Roles { get; } = roles;

    /// <summary>The role every public surface must assume for an anonymous reader of the agent card.</summary>
    public const string LeastPrivilegedRole = "customer";

    /// <summary>True when a caller holding <paramref name="role"/> may call a method with these roles.</summary>
    public static bool Allows(IReadOnlyList<string>? roles, string role) =>
        roles is null
        || roles.Contains(role, StringComparer.OrdinalIgnoreCase)
        || string.Equals(role, "admin", StringComparison.OrdinalIgnoreCase);
}
