using ECommerceAgents.Shared.Configuration;
using Microsoft.IdentityModel.Tokens;
using System.IdentityModel.Tokens.Jwt;
using System.Security.Claims;
using System.Security.Cryptography;
using System.Text;

namespace ECommerceAgents.Shared.Auth;

/// <summary>
/// Issues + parses self-contained JWTs for the frontend login flow.
/// The signing key is <see cref="AgentSettings.JwtSecret"/>; claims match
/// Python's <c>shared/jwt_utils.py</c> — <c>sub</c> (email), <c>email</c>,
/// <c>role</c>, <c>exp</c>.
/// </summary>
public sealed class JwtTokenService(AgentSettings settings)
{
    private readonly AgentSettings _settings = settings;

    /// <summary>
    /// <c>MapInboundClaims = false</c> is required: the default handler
    /// silently renames short claim types on validation (e.g. <c>sub</c> -&gt;
    /// the XML-namespace nameidentifier URI, <c>email</c> -&gt; the
    /// emailaddress URI, <c>role</c> -&gt; the MS role URI), so callers doing
    /// <c>principal.FindFirst("email")</c>/<c>FindFirst("role")</c> after
    /// <see cref="Validate"/>/<see cref="ValidateOAuth"/> would silently get
    /// null back instead of the actual claim.
    /// </summary>
    private readonly JwtSecurityTokenHandler _handler = new() { MapInboundClaims = false };
    public TimeSpan AccessTokenLifetime { get; init; } = TimeSpan.FromHours(24);
    public TimeSpan RefreshTokenLifetime { get; init; } = TimeSpan.FromDays(7);

    public string IssueAccessToken(string email, string role) => Issue(email, role, AccessTokenLifetime);

    public string IssueRefreshToken(string email, string role) => Issue(email, role, RefreshTokenLifetime);

    /// <summary>
    /// A refresh token that carries a <c>jti</c> (the <c>refresh_tokens</c> row key) and
    /// <c>type=refresh</c>, matching Python's claims, for <see cref="RefreshTokenStore"/>.
    /// </summary>
    public string IssueRefreshToken(string email, string role, string jti) =>
        Issue(email, role, RefreshTokenLifetime, new Claim("jti", jti), new Claim("type", "refresh"));

    public ClaimsPrincipal Validate(string token)
    {
        var parameters = new TokenValidationParameters
        {
            ValidateIssuer = false,
            ValidateAudience = false,
            ValidateLifetime = true,
            ValidateIssuerSigningKey = true,
            IssuerSigningKey = new SymmetricSecurityKey(DeriveKeyBytes(_settings.JwtSecret)),
            ClockSkew = TimeSpan.FromMinutes(1),
        };
        return _handler.ValidateToken(token, parameters, out _);
    }

    /// <summary>
    /// Validate an RS256 access token issued by the self-hosted auth-server
    /// (<c>AuthMode=oauth</c>) against its published JWKS. Throws a
    /// <see cref="SecurityTokenException"/> subclass on any failure —
    /// callers already handle that type for the local HS256 path.
    /// </summary>
    public ClaimsPrincipal ValidateOAuth(
        string token,
        IReadOnlyList<SecurityKey> signingKeys,
        string audience,
        string? requiredScope = null
    )
    {
        var parameters = new TokenValidationParameters
        {
            ValidateIssuer = true,
            ValidIssuer = _settings.AuthServerIssuer,
            ValidateAudience = true,
            ValidAudience = audience,
            ValidateLifetime = true,
            ValidateIssuerSigningKey = true,
            IssuerSigningKeys = signingKeys,
            ClockSkew = TimeSpan.FromMinutes(1),
        };
        var principal = _handler.ValidateToken(token, parameters, out _);

        if (requiredScope is not null)
        {
            var granted = (principal.FindFirst("scope")?.Value ?? string.Empty).Split(
                ' ',
                StringSplitOptions.RemoveEmptyEntries
            );
            if (!granted.Contains(requiredScope))
            {
                throw new SecurityTokenException($"token missing required scope '{requiredScope}'");
            }
        }

        return principal;
    }

    /// <summary>
    /// Build a 256-bit symmetric key from the JWT_SECRET env var. If the
    /// supplied secret is already long enough we use the raw bytes so
    /// tokens stay compatible with the Python backend's <c>PyJWT</c>
    /// output; otherwise we hash with SHA-256 to satisfy the MS
    /// Identity minimum key-size requirement. Python side is told to
    /// use the same derivation via the shared config module.
    /// </summary>
    private static byte[] DeriveKeyBytes(string secret)
    {
        var raw = Encoding.UTF8.GetBytes(secret);
        if (raw.Length >= 32)
        {
            return raw;
        }
        return SHA256.HashData(raw);
    }

    private string Issue(string email, string role, TimeSpan lifetime, params Claim[] extra)
    {
        var key = new SymmetricSecurityKey(DeriveKeyBytes(_settings.JwtSecret));
        var creds = new SigningCredentials(key, SecurityAlgorithms.HmacSha256);
        var now = DateTime.UtcNow;
        var token = new JwtSecurityToken(
            claims: new[]
            {
                new Claim("sub", email),
                new Claim("email", email),
                new Claim("role", role),
                new Claim("iat", new DateTimeOffset(now).ToUnixTimeSeconds().ToString(), ClaimValueTypes.Integer64),
            }.Concat(extra),
            notBefore: now,
            expires: now.Add(lifetime),
            signingCredentials: creds
        );
        return _handler.WriteToken(token);
    }
}
