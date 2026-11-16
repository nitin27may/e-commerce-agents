using ECommerceAgents.Orchestrator.Routes;
using ECommerceAgents.Shared.Context;
using FluentAssertions;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Http.HttpResults;
using Xunit;

namespace ECommerceAgents.Orchestrator.Tests;

/// <summary>D-2 — the .NET twin of Python's <c>require_staff</c> (support or admin, nobody else).</summary>
public sealed class StaffGuardTests
{
    [Theory]
    [InlineData("support")]
    [InlineData("admin")]
    [InlineData("SUPPORT")]
    public void IsStaff_AdmitsSupportAndAdmin(string role) => StaffGuard.IsStaff(role).Should().BeTrue();

    [Theory]
    [InlineData("customer")]
    [InlineData("power_user")]
    [InlineData("seller")]
    [InlineData("system")]
    [InlineData("")]
    [InlineData(null)]
    public void IsStaff_RefusesEveryoneElse(string? role) => StaffGuard.IsStaff(role).Should().BeFalse();

    [Theory]
    [InlineData("support")]
    [InlineData("admin")]
    public void Require_PassesStaff(string role)
    {
        RequestContext.CurrentUserEmail = "staff@example.com";
        RequestContext.CurrentUserRole = role;

        StaffGuard.Require().Should().BeNull();
    }

    [Theory]
    [InlineData("customer")]
    [InlineData("seller")]
    [InlineData("power_user")]
    public void Require_Returns403ForNonStaff(string role)
    {
        RequestContext.CurrentUserEmail = "someone@example.com";
        RequestContext.CurrentUserRole = role;

        StaffGuard.Require().Should().BeAssignableTo<IStatusCodeHttpResult>()
            .Which.StatusCode.Should().Be(403);
    }

    [Fact]
    public void Require_Returns401WithoutIdentity()
    {
        RequestContext.CurrentUserEmail = "";
        RequestContext.CurrentUserRole = "support";

        StaffGuard.Require().Should().BeOfType<UnauthorizedHttpResult>();
    }
}
