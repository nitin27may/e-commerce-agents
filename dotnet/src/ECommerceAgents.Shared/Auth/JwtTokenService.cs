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
    private readonly JwtSecurityTokenHandler _handler = new();
    public TimeSpan AccessTokenLifetime { get; init; } = TimeSpan.FromHours(24);
    public TimeSpan RefreshTokenLifetime { get; init; } = TimeSpan.FromDays(7);

    public string IssueAccessToken(string email, string role) => Issue(email, role, AccessTokenLifetime);

    public string IssueRefreshToken(string email, string role) => Issue(email, role, RefreshTokenLifetime);

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

    private string Issue(string email, string role, TimeSpan lifetime)
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
            },
            notBefore: now,
            expires: now.Add(lifetime),
            signingCredentials: creds
        );
        return _handler.WriteToken(token);
    }
}
