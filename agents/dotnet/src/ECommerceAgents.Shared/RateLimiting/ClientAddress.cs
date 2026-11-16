using System.Net;

namespace ECommerceAgents.Shared.RateLimiting;

/// <summary>
/// Resolves the caller's address for rate limiting - the .NET twin of Python's
/// <c>shared/rate_limit.py::_client_ip</c>.
/// </summary>
/// <remarks>
/// <c>X-Forwarded-For</c> is <c>client, proxy1, proxy2</c>; each hop appends the
/// address of whoever connected to it, so every entry except those written by
/// proxies we operate is caller-controlled. Taking the first entry (the old rule)
/// let anyone mint a fresh bucket per request. With <c>trustedHops = n</c> we take
/// the entry <c>n</c> places from the right. Falls back to the TCP peer when
/// <c>n</c> is 0, when the header is missing or shorter than <c>n</c>, or when the
/// chosen entry is not an IP literal.
/// </remarks>
public static class ClientAddress
{
    public static string? Resolve(string? forwardedFor, IPAddress? peer, int trustedHops)
    {
        var peerText = peer?.ToString();
        if (trustedHops <= 0 || string.IsNullOrWhiteSpace(forwardedFor))
        {
            return peerText;
        }

        var chain = forwardedFor.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
        if (chain.Length < trustedHops)
        {
            return peerText;
        }

        return IPAddress.TryParse(chain[^trustedHops], out var parsed) ? parsed.ToString() : peerText;
    }
}
