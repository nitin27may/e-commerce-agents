using System.IdentityModel.Tokens.Jwt;
using System.Security.Cryptography;
using System.Text;
using Microsoft.IdentityModel.Tokens;

namespace ECommerceAgents.Shared.Auth;

/// <summary>A user-context token that must not be believed. <see cref="Reason"/> is a fixed label, safe to log and return.</summary>
public sealed class IdentityTokenException(string reason) : Exception(reason)
{
    public string Reason { get; } = reason;
}

/// <summary>The identity a verified user-context token carries.</summary>
public sealed record UserContext(string Email, string Role, string SessionId, string Audience, long ExpiresAt);

/// <summary>
/// The signed user-context token: who the orchestrator says it is speaking for (B-14). The .NET twin of
/// Python's <c>shared/identity_token.py</c>; both are pinned by
/// <c>agents/python/config/identity/testdata/user-context.v1.json</c>, which each stack verifies.
/// </summary>
/// <remarks>
/// <para>
/// Until now a specialist trusted <c>X-User-Email</c> / <c>X-User-Role</c> / <c>X-Session-Id</c> from anyone holding
/// the inter-agent credential. The credential says which <em>agent</em> is calling; the headers then said which
/// <em>user</em>, with nothing binding the second claim to the first. The orchestrator now signs the user's identity
/// into a short-lived JWT, sent as <c>X-User-Context</c>, and a specialist verifies it before believing any of it.
/// </para>
/// <para>
/// <b>Format.</b> JWS compact, <c>alg=HS256</c>, header <c>typ=a2a-user-context+jwt</c> (a user's login JWT, also
/// HS256, can never be mistaken for one). Claims: <c>iss</c> = <c>ecommerce-agents/orchestrator</c>; <c>aud</c> = the
/// specialist's registry name (a token for one specialist is no use at another); <c>sub</c> = the email, or
/// <c>anonymous</c>; <c>email</c> and <c>role</c> exactly as the headers would carry them; <c>sid</c> the session id;
/// <c>act</c> = <c>{"client_id":"orchestrator"}</c> (RFC 8693); <c>iat</c>, <c>exp</c>, <c>jti</c>. The lifetime is
/// <c>A2A_IDENTITY_TTL_SECONDS</c> (default five minutes) and a verifier refuses any longer than
/// <see cref="MaxLifetimeSeconds"/>.
/// </para>
/// <para>
/// <b>Key.</b> HMAC-SHA256 over a key derived with HKDF-SHA256 (RFC 5869, no salt) from <c>A2A_IDENTITY_KEY</c> if set,
/// else <c>AGENT_SHARED_SECRET</c>, with a fixed info string. No new identity provider, nothing new to distribute in
/// the default setup. The key is symmetric, so a specialist could also mint a token: a <em>compromised specialist</em>
/// is not stopped by this. What it adds is that a request cannot be replayed at another specialist or after it
/// expires, that a header forged beside a valid token is refused, and that, with <c>A2A_IDENTITY_KEY</c> different
/// from the shared secret, a caller holding only <c>X-Agent-Secret</c> can no longer choose its user.
/// </para>
/// </remarks>
public static class UserContextToken
{
    public const string TokenType = "a2a-user-context+jwt";
    public const string Issuer = "ecommerce-agents/orchestrator";
    public const string ActorClientId = "orchestrator";
    public const string HeaderName = "X-User-Context";
    public const string KdfInfo = "ecommerce-agents/a2a-user-context/v1";

    /// <summary>A verifier refuses a token whose own exp - iat is longer than this, whatever TTL the signer used.</summary>
    public const int MaxLifetimeSeconds = 900;

    /// <summary>Clock skew tolerated between the processes.</summary>
    public const int LeewaySeconds = 30;

    private static readonly JwtSecurityTokenHandler Handler = new() { MapInboundClaims = false };

    /// <summary>The 32-byte signing key for <paramref name="secret"/>; identical on both stacks (shared test vectors).</summary>
    public static byte[] DeriveKey(string secret) =>
        HKDF.DeriveKey(HashAlgorithmName.SHA256, Encoding.UTF8.GetBytes(secret), 32, salt: null, info: Encoding.UTF8.GetBytes(KdfInfo));

    /// <summary>The key a process signs and verifies with: <c>A2A_IDENTITY_KEY</c>, else <c>AGENT_SHARED_SECRET</c>.</summary>
    public static byte[] ConfiguredKey(Configuration.AgentSettings settings) =>
        DeriveKey(string.IsNullOrWhiteSpace(settings.A2aIdentityKey) ? settings.AgentSharedSecret : settings.A2aIdentityKey.Trim());

    /// <summary>Signs the identity of the user the orchestrator is speaking for, for one specialist.</summary>
    public static string Mint(
        string email,
        string role,
        string sessionId,
        string audience,
        byte[] key,
        int ttlSeconds = 300,
        long? now = null,
        string? jti = null)
    {
        var issued = now ?? DateTimeOffset.UtcNow.ToUnixTimeSeconds();
        var header = new JwtHeader(new SigningCredentials(new SymmetricSecurityKey(key), SecurityAlgorithms.HmacSha256))
        {
            ["typ"] = TokenType,
        };
        var payload = new JwtPayload
        {
            ["iss"] = Issuer,
            ["aud"] = audience,
            ["sub"] = string.IsNullOrEmpty(email) ? "anonymous" : email,
            ["email"] = email,
            ["role"] = role,
            ["sid"] = sessionId,
            ["act"] = new Dictionary<string, object> { ["client_id"] = ActorClientId },
            ["iat"] = issued,
            ["exp"] = issued + ttlSeconds,
            ["jti"] = jti ?? Guid.NewGuid().ToString("N"),
        };
        return Handler.WriteToken(new JwtSecurityToken(header, payload));
    }

    /// <summary>
    /// Verifies <paramref name="token"/> for the specialist named <paramref name="audience"/> and returns the identity
    /// it carries; throws <see cref="IdentityTokenException"/> with a fixed reason for anything that must not be
    /// believed (malformed, wrong algorithm or type, bad signature, expired, wrong audience or issuer, a lifetime
    /// over <see cref="MaxLifetimeSeconds"/>, missing claims). Nothing of the token's content is in the reason.
    /// </summary>
    public static UserContext Verify(string token, string audience, byte[] key, long? now = null)
    {
        JwtSecurityToken parsed;
        try
        {
            parsed = Handler.ReadJwtToken(token);
        }
        catch (Exception ex) when (ex is ArgumentException or SecurityTokenException)
        {
            throw new IdentityTokenException("malformed");
        }

        if (parsed.Header.Alg != SecurityAlgorithms.HmacSha256 || parsed.Header.Typ != TokenType)
        {
            throw new IdentityTokenException("wrong_type");
        }

        var instant = now ?? DateTimeOffset.UtcNow.ToUnixTimeSeconds();
        var parameters = new TokenValidationParameters
        {
            ValidateIssuer = true,
            ValidIssuer = Issuer,
            ValidateAudience = true,
            ValidAudience = audience,
            ValidateIssuerSigningKey = true,
            IssuerSigningKey = new SymmetricSecurityKey(key),
            ValidAlgorithms = [SecurityAlgorithms.HmacSha256],
            RequireSignedTokens = true,
            RequireExpirationTime = true,
            ValidateLifetime = true,
            ClockSkew = TimeSpan.FromSeconds(LeewaySeconds),
            // The same instant the caller asked about, so a test (and the shared vectors) can fix the clock.
            LifetimeValidator = (_, expires, _, p) =>
                expires is { } e && DateTimeOffset.FromUnixTimeSeconds(instant) <= new DateTimeOffset(e, TimeSpan.Zero) + p.ClockSkew,
        };

        JwtSecurityToken validated;
        try
        {
            Handler.ValidateToken(token, parameters, out var securityToken);
            validated = (JwtSecurityToken)securityToken;
        }
        catch (SecurityTokenExpiredException)
        {
            throw new IdentityTokenException("expired");
        }
        catch (SecurityTokenInvalidAudienceException)
        {
            throw new IdentityTokenException("audience");
        }
        catch (SecurityTokenInvalidIssuerException)
        {
            throw new IdentityTokenException("issuer");
        }
        catch (Exception ex) when (ex is SecurityTokenInvalidSignatureException or SecurityTokenSignatureKeyNotFoundException)
        {
            throw new IdentityTokenException("signature");
        }
        catch (Exception ex) when (ex is SecurityTokenException or ArgumentException)
        {
            // SecurityTokenNoExpirationException, a lifetime validator that said no, anything else about the token.
            throw new IdentityTokenException(
                ex is SecurityTokenInvalidLifetimeException ? "expired" : "malformed");
        }

        var payload = validated.Payload;
        long? Number(string name) => payload.TryGetValue(name, out var v)
            ? v switch { long l => l, int i => i, double d when d == Math.Floor(d) => (long)d, _ => null }
            : null;

        if (Number("exp") is not { } exp || Number("iat") is not { } iat)
        {
            throw new IdentityTokenException("malformed");
        }

        if (exp - iat > MaxLifetimeSeconds)
        {
            throw new IdentityTokenException("lifetime");
        }

        string? Text(string name) => payload.TryGetValue(name, out var v) && v is string s ? s : null;
        var email = Text("email");
        var role = Text("role");
        var sid = payload.ContainsKey("sid") ? Text("sid") : string.Empty;
        if (email is null || role is null || sid is null)
        {
            throw new IdentityTokenException("malformed");
        }

        return new UserContext(email, role, sid, audience, exp);
    }
}
