using ECommerceAgents.Shared.Configuration;
using System.IdentityModel.Tokens.Jwt;
using Microsoft.IdentityModel.Tokens;

namespace ECommerceAgents.Shared.Auth;

/// <summary>
/// Hand-rolled JWKS fetch-and-cache for validating RS256 tokens issued by
/// the self-hosted auth-server (<c>AuthMode=oauth</c>).
/// </summary>
/// <remarks>
/// Deliberately NOT <c>ConfigurationManager&lt;OpenIdConnectConfiguration&gt;</c> —
/// the auth-server serves RFC 8414 metadata
/// (<c>/.well-known/oauth-authorization-server</c>), not OIDC discovery
/// (<c>/.well-known/openid-configuration</c>), so that type's default
/// metadata-refresh path would 404. A plain <c>GET</c> of
/// <see cref="AgentSettings.AuthServerJwksUrl"/> plus a TTL cache is the
/// entire feature surface actually needed here.
///
/// Signing-key rotation: the auth-server publishes a new key alongside the old one. A token naming a
/// <c>kid</c> the cache does not hold makes <see cref="GetSigningKeysForTokenAsync"/> refetch the JWKS once,
/// at most once per <see cref="UnknownKidRefreshCooldown"/> per process, so a rotation needs no restart and a
/// stream of garbage <c>kid</c> values cannot turn every request into a fetch.
/// </remarks>
public sealed class JwksKeyProvider(HttpClient httpClient, AgentSettings settings, TimeProvider? timeProvider = null)
{
    /// <summary>Minimum gap between forced refreshes triggered by an unknown <c>kid</c>.</summary>
    public static readonly TimeSpan UnknownKidRefreshCooldown = TimeSpan.FromSeconds(30);

    private static readonly JwtSecurityTokenHandler KidReader = new();

    private readonly HttpClient _httpClient = httpClient;
    private readonly AgentSettings _settings = settings;
    private readonly TimeProvider _time = timeProvider ?? TimeProvider.System;
    private readonly SemaphoreSlim _refreshLock = new(1, 1);

    private IReadOnlyList<SecurityKey>? _cachedKeys;
    private DateTimeOffset _cachedAt = DateTimeOffset.MinValue;
    private DateTimeOffset? _lastForcedRefresh;

    public async Task<IReadOnlyList<SecurityKey>> GetSigningKeysAsync(CancellationToken ct = default)
    {
        var ttl = TimeSpan.FromSeconds(_settings.AuthJwksCacheTtl);
        if (_cachedKeys is not null && _time.GetUtcNow() - _cachedAt < ttl)
        {
            return _cachedKeys;
        }

        await _refreshLock.WaitAsync(ct);
        try
        {
            // Another request may have refreshed the cache while we were
            // waiting for the lock — check again before hitting the network.
            if (_cachedKeys is not null && _time.GetUtcNow() - _cachedAt < ttl)
            {
                return _cachedKeys;
            }

            var json = await _httpClient.GetStringAsync(_settings.AuthServerJwksUrl, ct);
            var jwks = new JsonWebKeySet(json);
            _cachedKeys = jwks.Keys.Cast<SecurityKey>().ToList();
            _cachedAt = _time.GetUtcNow();
            return _cachedKeys;
        }
        finally
        {
            _refreshLock.Release();
        }
    }

    /// <summary>
    /// The signing keys to validate <paramref name="token"/> against. When the token names a <c>kid</c> the
    /// cached JWKS lacks, refetch once (rate-limited) so a freshly rotated key is found; otherwise identical to
    /// <see cref="GetSigningKeysAsync"/>. Never throws for an unreadable token or a failed refetch: the caller's
    /// validation then rejects it against whatever keys are cached.
    /// </summary>
    public async Task<IReadOnlyList<SecurityKey>> GetSigningKeysForTokenAsync(
        string token,
        CancellationToken ct = default
    )
    {
        var keys = await GetSigningKeysAsync(ct);
        var kid = ReadKid(token);
        if (string.IsNullOrEmpty(kid) || keys.Any(k => k.KeyId == kid))
        {
            return keys;
        }

        await _refreshLock.WaitAsync(ct);
        try
        {
            // A concurrent request may have refreshed while we waited for the lock.
            if (_cachedKeys is not null && _cachedKeys.Any(k => k.KeyId == kid))
            {
                return _cachedKeys;
            }

            var now = _time.GetUtcNow();
            if (_lastForcedRefresh is { } last && now - last < UnknownKidRefreshCooldown)
            {
                return _cachedKeys ?? keys;
            }

            // Stamp before fetching: an unreachable auth-server must not be hammered either.
            _lastForcedRefresh = now;
            try
            {
                var json = await _httpClient.GetStringAsync(_settings.AuthServerJwksUrl, ct);
                _cachedKeys = new JsonWebKeySet(json).Keys.Cast<SecurityKey>().ToList();
                _cachedAt = now;
            }
            catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException or ArgumentException)
            {
                // Fall through with the cached keys; validation fails closed for the unknown kid.
            }

            return _cachedKeys ?? keys;
        }
        finally
        {
            _refreshLock.Release();
        }
    }

    private static string? ReadKid(string token)
    {
        try
        {
            return KidReader.CanReadToken(token) ? KidReader.ReadJwtToken(token).Header.Kid : null;
        }
        catch (ArgumentException)
        {
            return null;
        }
    }
}
