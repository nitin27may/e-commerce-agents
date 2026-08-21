using FluentAssertions;
using System.Text.RegularExpressions;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// Policy test for issue #32: a destructive tool must not ship ungated.
///
/// Python enforces roles with a <c>@requires_role</c> decorator, so the
/// requirement is part of the tool's declaration. .NET uses a guard clause
/// inside each method body instead — which is equivalent where applied, but
/// opt-in: a newly added destructive tool is ungated until somebody remembers,
/// and nothing catches the omission.
///
/// A pipeline stage would be the closer analogue to the decorator, but every
/// tool here returns its own typed result record (<c>CancelOrderResult</c>,
/// <c>PlaceBackorderResult</c>, …) built through a private
/// <c>Failure(string)</c> factory, so a single interceptor has no way to
/// produce the right denial shape for an arbitrary tool. Rather than weaken
/// those return types, this asserts the policy directly against the source:
/// every tool whose name reads as a mutation either enforces a role or is
/// explicitly recorded here as intentionally open, with a reason.
///
/// Adding a destructive tool without a role check now fails the build.
/// </summary>
public sealed class DestructiveToolRoleGatingTests
{
    /// <summary>Verbs that imply the tool changes state rather than reading it.</summary>
    private static readonly string[] MutatingPrefixes =
    [
        "Cancel", "Modify", "Place", "Initiate", "Process", "Create", "Delete",
        "Remove", "Update", "Set", "Add", "Apply", "Draft", "Submit", "Approve",
    ];

    /// <summary>
    /// Mutating tools that deliberately carry no role check, each with the
    /// reason. Anything added here should be a considered decision, not a
    /// convenient way to silence the test.
    /// </summary>
    private static readonly Dictionary<string, string> IntentionallyUngated = new()
    {
        // Cart mutations act on the caller's own cart, scoped by
        // RequestContext identity — every role including "customer" may do
        // this, so a role gate would reject nobody.
    };

    private static string RepoRoot()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !Directory.Exists(Path.Combine(dir.FullName, "agents", "dotnet")))
        {
            dir = dir.Parent;
        }
        dir.Should().NotBeNull("the test must be able to locate the repository root");
        return dir!.FullName;
    }

    [Fact]
    public void EveryMutatingTool_EnforcesARoleOrIsRecordedAsIntentionallyOpen()
    {
        var srcRoot = Path.Combine(RepoRoot(), "agents", "dotnet", "src");
        var toolFiles = Directory.GetFiles(srcRoot, "*Tools.cs", SearchOption.AllDirectories);
        toolFiles.Should().NotBeEmpty("tool classes must be discoverable for this policy to mean anything");

        var ungated = new List<string>();

        foreach (var file in toolFiles)
        {
            var source = File.ReadAllText(file);

            // Only tools actually registered with the model are in scope —
            // private helpers are not reachable by an agent.
            var registered = Regex.Matches(source, @"AIFunctionFactory\.Create\(\s*\w+\s*,\s*nameof\((?<name>\w+)\)")
                .Select(m => m.Groups["name"].Value)
                .ToHashSet();

            foreach (var tool in registered.Where(IsMutating))
            {
                if (IntentionallyUngated.ContainsKey(tool))
                {
                    continue;
                }

                if (!MethodBody(source, tool).Contains("RoleGuard.Ensure"))
                {
                    ungated.Add($"{Path.GetFileName(file)}::{tool}");
                }
            }
        }

        ungated.Should().BeEmpty(
            "a mutating tool must call RoleGuard.Ensure, or be recorded in IntentionallyUngated with a reason"
        );
    }

    private static bool IsMutating(string toolName) =>
        MutatingPrefixes.Any(p => toolName.StartsWith(p, StringComparison.Ordinal));

    /// <summary>
    /// Extracts a method body by brace matching from its signature, so a role
    /// check in a *neighbouring* tool can't be mistaken for this one's.
    /// </summary>
    private static string MethodBody(string source, string methodName)
    {
        var signature = Regex.Match(source, $@"\b{Regex.Escape(methodName)}\s*\(");
        if (!signature.Success)
        {
            return string.Empty;
        }

        var open = source.IndexOf('{', signature.Index);
        if (open < 0)
        {
            return string.Empty;
        }

        var depth = 0;
        for (var i = open; i < source.Length; i++)
        {
            if (source[i] == '{') depth++;
            else if (source[i] == '}' && --depth == 0)
            {
                return source[open..i];
            }
        }

        return source[open..];
    }
}
