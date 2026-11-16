using System.Text.RegularExpressions;
using ECommerceAgents.Shared.Guardrails;
using FluentAssertions;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// The public A2A agent card lists only tools the least-privileged role may call, and it learns that from
/// <see cref="RequiresRoleAttribute"/>. Enforcement is the <c>RoleGuard.Ensure</c> call in the method body
/// (a guard clause, see <see cref="RoleGuard"/>), so the two must say the same thing: this reads the source,
/// the way <c>DestructiveToolRoleGatingTests</c> does, and fails when a guard has no matching attribute (the card
/// would then advertise a tool its reader cannot use) or the roles differ.
/// </summary>
public sealed class A2ARoleMetadataTests
{
    private static readonly Regex Ensure = new(@"RoleGuard\.Ensure\(\s*_settings\s*,(?<roles>[^)]*)\)", RegexOptions.Compiled);
    private static readonly Regex RoleLiteral = new("\"(?<r>[a-z_]+)\"", RegexOptions.Compiled);
    private static readonly Regex Attribute = new(@"RequiresRole\((?<roles>[^)]*)\)", RegexOptions.Compiled);

    private static string SrcRoot()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !Directory.Exists(Path.Combine(dir.FullName, "agents", "dotnet", "src")))
        {
            dir = dir.Parent;
        }

        dir.Should().NotBeNull("the test must be able to locate the repository root");
        return Path.Combine(dir!.FullName, "agents", "dotnet", "src");
    }

    private static string[] Roles(string literal) =>
        RoleLiteral.Matches(literal).Select(m => m.Groups["r"].Value).Order().ToArray();

    [Fact]
    public void EveryRoleGuard_HasAMatchingRequiresRoleAttributeOnItsMethod()
    {
        var found = 0;
        foreach (var file in Directory.EnumerateFiles(SrcRoot(), "*.cs", SearchOption.AllDirectories)
                     .Where(f => !f.Contains($"{Path.DirectorySeparatorChar}obj{Path.DirectorySeparatorChar}")
                                 && !f.EndsWith("RoleGuard.cs", StringComparison.Ordinal)))
        {
            var lines = File.ReadAllLines(file);
            for (var i = 0; i < lines.Length; i++)
            {
                var guard = Ensure.Match(lines[i]);
                if (!guard.Success)
                {
                    continue;
                }

                found++;

                // The method's own declaration: the nearest `public` line above the guard.
                var decl = i;
                while (decl >= 0 && !Regex.IsMatch(lines[decl], @"^\s*public\s"))
                {
                    decl--;
                }

                decl.Should().BeGreaterThan(-1, $"{file}:{i + 1} sits in a public method");

                // Attributes sit directly above it, up to a blank line or a closing brace.
                var attributes = new List<string>();
                for (var a = decl - 1; a >= 0 && lines[a].Trim().Length > 0 && !lines[a].Trim().StartsWith('}'); a--)
                {
                    attributes.Add(lines[a]);
                }

                var declared = attributes.Select(l => Attribute.Match(l)).FirstOrDefault(m => m.Success);
                declared.Should().NotBeNull(
                    $"{Path.GetFileName(file)}:{i + 1} calls RoleGuard.Ensure but its method has no [RequiresRole]; "
                    + "the public agent card would advertise a tool its reader cannot call");
                Roles(declared!.Groups["roles"].Value).Should().Equal(
                    Roles(guard.Groups["roles"].Value),
                    $"{Path.GetFileName(file)}:{i + 1}: [RequiresRole] and RoleGuard.Ensure disagree");
            }
        }

        found.Should().BeGreaterThan(5, "the scan must actually find the guards it is checking");
    }

    [Theory]
    [InlineData(new[] { "seller", "admin" }, "customer", false)]
    [InlineData(new[] { "customer", "seller" }, "customer", true)]
    [InlineData(new[] { "seller" }, "admin", true)]
    [InlineData(null, "customer", true)]
    public void Allows(string[]? roles, string role, bool expected) =>
        RequiresRoleAttribute.Allows(roles, role).Should().Be(expected);
}
