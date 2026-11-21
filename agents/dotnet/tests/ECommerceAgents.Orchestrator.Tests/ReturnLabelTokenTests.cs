using ECommerceAgents.Orchestrator.Routes;
using FluentAssertions;
using Xunit;

namespace ECommerceAgents.Orchestrator.Tests;

/// <summary>SEC-1 — the public label route accepts only a full 12-char lowercase hex token (no LIKE wildcards).</summary>
public sealed class ReturnLabelTokenTests
{
    [Theory]
    [InlineData("a1b2c3d4e5f6")]
    [InlineData("000000000000")]
    public void WellFormed_AcceptsTheMintedFormat(string token) =>
        ReturnLabelRoutes.IsWellFormedToken(token).Should().BeTrue();

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("%")]
    [InlineData("_")]
    [InlineData("____________")]
    [InlineData("%b2c3d4e5f6")]
    [InlineData("a1b2c3")]
    [InlineData("a1b2c3d4e5f6a")]
    [InlineData("A1B2C3D4E5F6")]
    [InlineData("a1b2c3d4e5f%")]
    public void WellFormed_RefusesEverythingElse(string? token) =>
        ReturnLabelRoutes.IsWellFormedToken(token).Should().BeFalse();
}
