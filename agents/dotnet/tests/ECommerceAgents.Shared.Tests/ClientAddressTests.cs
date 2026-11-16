using System.Net;
using ECommerceAgents.Shared.RateLimiting;
using FluentAssertions;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>Mirrors Python's <c>_client_ip</c> tests (tests/test_rate_limit_auth.py).</summary>
public sealed class ClientAddressTests
{
    private static readonly IPAddress Peer = IPAddress.Parse("172.18.0.5");

    [Fact]
    public void TakesTheRightMostEntryByDefault_IgnoringCallerSuppliedOnes()
    {
        ClientAddress.Resolve("10.0.0.1, 198.51.100.1", Peer, 1).Should().Be("198.51.100.1");
    }

    [Fact]
    public void TwoHopsTakesTheSecondFromTheRight()
    {
        ClientAddress.Resolve("6.6.6.6, 198.51.100.2, 192.0.2.50", Peer, 2).Should().Be("198.51.100.2");
    }

    [Theory]
    [InlineData(null, 1)]
    [InlineData("", 1)]
    [InlineData("198.51.100.3", 0)]
    [InlineData("198.51.100.3", 2)]
    [InlineData("evil:key*", 1)]
    public void FallsBackToThePeer(string? header, int hops)
    {
        ClientAddress.Resolve(header, Peer, hops).Should().Be("172.18.0.5");
    }

    [Fact]
    public void NoPeerAndNoHeaderIsNull()
    {
        ClientAddress.Resolve(null, null, 1).Should().BeNull();
    }
}
