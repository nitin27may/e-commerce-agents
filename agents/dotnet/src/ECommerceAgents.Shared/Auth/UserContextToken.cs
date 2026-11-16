using System.IdentityModel.Tokens.Jwt;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
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
/// is not stopped by this. What it adds is that a request cannot be replayed at <em>another</em> specialist or after it
/// expires (at the <em>same</em> specialist it can be replayed until then: <c>jti</c> is minted for audit correlation
/// and nothing checks it) and that a header forged beside a valid token is refused. A caller holding only
/// <c>X-Agent-Secret</c> can no longer choose its user <b>only when both</b> hold: <c>A2A_IDENTITY=token</c> (the
/// default, <c>both</c>, still accepts a request with no token and believes its <c>X-User-*</c> headers) <b>and</b>
/// <c>A2A_IDENTITY_KEY</c> is different from the shared secret. A process in <c>token</c> mode without a distinct key
/// logs a startup warning.
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
            // Every time rule is applied below against one instant (the caller's, so a test and the shared vectors can
            // pin the clock) and the way Python applies it: exp, iat and nbf, whole numbers only.
            ValidateLifetime = false,
            RequireExpirationTime = false,
        };

        JwtSecurityToken validated;
        try
        {
            Handler.ValidateToken(token, parameters, out var securityToken);
            validated = (JwtSecurityToken)securityToken;
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
            throw new IdentityTokenException("malformed");
        }

        // The signature has been checked, so the payload is read as raw JSON: that is the only way to tell 1700000000
        // from 1700000000.0, which Python refuses (a time claim must be a whole number on both stacks).
        JsonElement claims;
        try
        {
            claims = JsonDocument.Parse(Base64UrlEncoder.DecodeBytes(validated.RawPayload)).RootElement.Clone();
        }
        catch (Exception ex) when (ex is JsonException or FormatException or ArgumentException)
        {
            throw new IdentityTokenException("malformed");
        }

        if (claims.ValueKind != JsonValueKind.Object)
        {
            throw new IdentityTokenException("malformed");
        }

        static bool WholeNumber(JsonElement c, string name, out long value, out bool present)
        {
            value = 0;
            present = c.TryGetProperty(name, out var element);
            return present && element.ValueKind == JsonValueKind.Number && element.TryGetInt64(out value)
                && !element.GetRawText().AsSpan().ContainsAny('.', 'e', 'E');
        }

        string? Text(string name) => claims.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;

        var hasNbf = WholeNumber(claims, "nbf", out var nbf, out var nbfPresent);
        if (!WholeNumber(claims, "exp", out var exp, out _) || !WholeNumber(claims, "iat", out var iat, out _)
            || (nbfPresent && !hasNbf) || Text("sub") is null)
        {
            throw new IdentityTokenException("malformed");
        }

        if (exp + LeewaySeconds < instant)
        {
            throw new IdentityTokenException("expired");
        }

        if (iat > instant + LeewaySeconds || (nbfPresent && nbf > instant + LeewaySeconds))
        {
            throw new IdentityTokenException("not_yet_valid");
        }

        if (exp - iat > MaxLifetimeSeconds)
        {
            throw new IdentityTokenException("lifetime");
        }

        var email = Text("email");
        var role = Text("role");
        var sid = claims.TryGetProperty("sid", out _) ? Text("sid") : string.Empty;
        if (email is null || role is null || sid is null)
        {
            throw new IdentityTokenException("malformed");
        }

        return new UserContext(email, role, sid, audience, exp);
    }
}
