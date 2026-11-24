using System.Net;
using ECommerceAgents.Shared.Configuration;
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

    private static readonly IReadOnlyList<IPNetwork>? Gate = ClientAddress.ParseTrustedProxies("172.16.0.0/12, 10.0.0.0/8");

    [Fact]
    public void DefaultHopsIsZero_SoTheHeaderIsIgnored()
    {
        new AgentSettings().RateLimitTrustedProxyHops.Should().Be(0);
        ClientAddress.Resolve("6.6.6.6", Peer, new AgentSettings()).Should().Be("172.18.0.5");
    }

    [Fact]
    public void SpoofedHeaderFromAnUntrustedPeerIsIgnored()
    {
        ClientAddress.Resolve("6.6.6.6", IPAddress.Parse("198.51.100.9"), 1, Gate).Should().Be("198.51.100.9");
    }

    [Fact]
    public void HeaderFromATrustedProxyIsHonoured_RightMostWins()
    {
        ClientAddress.Resolve("203.0.113.7", Peer, 1, Gate).Should().Be("203.0.113.7");
        ClientAddress.Resolve("6.6.6.6, 203.0.113.7", Peer, 1, Gate).Should().Be("203.0.113.7");
        ClientAddress.Resolve("6.6.6.6, 203.0.113.7, 10.1.1.1", Peer, 2, Gate).Should().Be("203.0.113.7");
    }

    [Fact]
    public void IPv4MappedIPv6PeerMatchesAnIPv4Network()
    {
        ClientAddress.Resolve("203.0.113.7", IPAddress.Parse("::ffff:172.18.0.5"), 1, Gate).Should().Be("203.0.113.7");
    }

    [Theory]
    [InlineData("not-an-ip")]
    [InlineData("evil:key*")]
    [InlineData("999.1.1.1")]
    [InlineData(",,")]
    public void MalformedHeaderFallsBackToThePeer(string header)
    {
        ClientAddress.Resolve(header, Peer, 1, Gate).Should().Be("172.18.0.5");
    }

    [Fact]
    public void NoGateConfiguredLetsHopsDecide_AndAGateThatParsesToNothingTrustsNobody()
    {
        ClientAddress.ParseTrustedProxies("").Should().BeNull();
        ClientAddress.Resolve("203.0.113.7", IPAddress.Parse("198.51.100.9"), 1, null).Should().Be("203.0.113.7");

        var none = ClientAddress.ParseTrustedProxies("garbage, also-garbage");
        none.Should().BeEmpty();
        ClientAddress.Resolve("6.6.6.6", Peer, 1, none).Should().Be("172.18.0.5");
    }

    [Fact]
    public void HostBitsAreMaskedAndABareAddressIsASingleHost()
    {
        var nets = ClientAddress.ParseTrustedProxies("172.18.0.5/12, 192.0.2.1");
        ClientAddress.Resolve("203.0.113.7", Peer, 1, nets).Should().Be("203.0.113.7");
        ClientAddress.Resolve("203.0.113.7", IPAddress.Parse("192.0.2.1"), 1, nets).Should().Be("203.0.113.7");
        ClientAddress.Resolve("203.0.113.7", IPAddress.Parse("192.0.2.2"), 1, nets).Should().Be("192.0.2.2");
    }
}
