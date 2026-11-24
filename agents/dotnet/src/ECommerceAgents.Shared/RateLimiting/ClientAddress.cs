using System.Collections.Concurrent;
using System.Net;
using ECommerceAgents.Shared.Configuration;

namespace ECommerceAgents.Shared.RateLimiting;

/// <summary>
/// Resolves the caller's address for rate limiting - the .NET twin of Python's
/// <c>shared/rate_limit.py::resolve_client_ip</c>.
/// </summary>
/// <remarks>
/// <c>X-Forwarded-For</c> is <c>client, proxy1, proxy2</c>; each hop appends the
/// address of whoever connected to it, so every entry except those written by
/// proxies we operate is caller-controlled. Taking the first entry (the old rule)
/// let anyone mint a fresh bucket per request. With <c>trustedHops = n</c> we take
/// the entry <c>n</c> places from the right, never the left-most. Falls back to the
/// TCP peer when <c>n</c> is 0, when a trusted-proxy gate is configured and the peer
/// is not in it (a caller reaching us directly), when the header is missing or
/// shorter than <c>n</c>, or when the chosen entry is not an IP literal.
/// </remarks>
public static class ClientAddress
{
    private static readonly ConcurrentDictionary<string, IReadOnlyList<IPNetwork>?> GateCache = new();

    public static string? Resolve(string? forwardedFor, IPAddress? peer, AgentSettings settings) =>
        Resolve(forwardedFor, peer, settings.RateLimitTrustedProxyHops, ParseTrustedProxies(settings.RateLimitTrustedProxies));

    public static string? Resolve(
        string? forwardedFor,
        IPAddress? peer,
        int trustedHops,
        IReadOnlyList<IPNetwork>? trustedProxies = null)
    {
        var peerText = peer?.ToString();
        if (trustedHops <= 0 || string.IsNullOrWhiteSpace(forwardedFor))
        {
            return peerText;
        }

        if (trustedProxies is not null)
        {
            if (peer is null)
            {
                return peerText;
            }

            var normalised = peer.IsIPv4MappedToIPv6 ? peer.MapToIPv4() : peer;
            if (!trustedProxies.Any(n => n.Contains(normalised)))
            {
                return peerText;
            }
        }

        var chain = forwardedFor.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
        if (chain.Length < trustedHops)
        {
            return peerText;
        }

        return IPAddress.TryParse(chain[^trustedHops], out var parsed) ? parsed.ToString() : peerText;
    }

    /// <summary>
    /// Parses <c>RATE_LIMIT_TRUSTED_PROXIES</c>. <c>null</c> = no gate configured; an empty list = trust nobody.
    /// A malformed entry is dropped, which can only narrow trust; if every entry is malformed the result is the
    /// empty list, never <c>null</c>: a typo must not switch the gate off.
    /// </summary>
    public static IReadOnlyList<IPNetwork>? ParseTrustedProxies(string? raw)
    {
        if (string.IsNullOrWhiteSpace(raw))
        {
            return null;
        }

        return GateCache.GetOrAdd(raw, static value =>
        {
            var entries = value.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
            var networks = new List<IPNetwork>();
            foreach (var entry in entries)
            {
                if (TryParseNetwork(entry, out var net))
                {
                    networks.Add(net);
                }
            }

            return networks;
        });
    }

    // Same leniency as Python's ip_network(strict=False): a bare address is a single host and host bits are masked off.
    private static bool TryParseNetwork(string entry, out IPNetwork network)
    {
        network = default;
        var parts = entry.Split('/', 2);
        if (!IPAddress.TryParse(parts[0], out var address))
        {
            return false;
        }

        var bytes = address.GetAddressBytes();
        var maxPrefix = bytes.Length * 8;
        var prefix = maxPrefix;
        if (parts.Length == 2 && (!int.TryParse(parts[1], out prefix) || prefix < 0 || prefix > maxPrefix))
        {
            return false;
        }

        for (var i = 0; i < bytes.Length; i++)
        {
            var bitsKept = Math.Clamp(prefix - (i * 8), 0, 8);
            bytes[i] &= (byte)(0xFF << (8 - bitsKept));
        }

        network = new IPNetwork(new IPAddress(bytes), prefix);
        return true;
    }
}
