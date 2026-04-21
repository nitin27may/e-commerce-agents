using ECommerceAgents.Shared.Configuration;
using Microsoft.IdentityModel.Tokens;
using System.IdentityModel.Tokens.Jwt;
using System.Security.Claims;
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
            IssuerSigningKey = new SymmetricSecurityKey(Encoding.UTF8.GetBytes(_settings.JwtSecret)),
            ClockSkew = TimeSpan.FromMinutes(1),
        };
        return _handler.ValidateToken(token, parameters, out _);
    }

    private string Issue(string email, string role, TimeSpan lifetime)
    {
        var key = new SymmetricSecurityKey(Encoding.UTF8.GetBytes(_settings.JwtSecret));
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
