using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.Logging;
using Microsoft.IdentityModel.Tokens;
using System.IdentityModel.Tokens.Jwt;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace ECommerceAgents.Shared.Auth;

/// <summary>
/// Mirrors Python's <c>shared/auth.py</c>. Accepts two call patterns:
/// <list type="number">
/// <item>External: <c>Authorization: Bearer &lt;JWT&gt;</c> — validates signature and extracts the <c>email</c> + <c>role</c> claims.</item>
/// <item>Inter-agent (A2A): <c>X-Agent-Secret</c> header equals <see cref="AgentSettings.AgentSharedSecret"/>; the caller provides the user via <c>X-User-Email</c> / <c>X-User-Role</c> / <c>X-Session-Id</c> headers.</item>
/// </list>
/// Before the downstream handler runs the middleware stamps the
/// <see cref="RequestContext"/> AsyncLocal slots so tools can read the
/// current identity without threading it through call stacks.
/// </summary>
public sealed class AgentAuthMiddleware
{
    private static readonly HashSet<string> _publicPaths = new(StringComparer.OrdinalIgnoreCase)
    {
        "/",
        "/health",
        "/.well-known/agent-card.json",
        "/api/auth/signup",
        "/api/auth/login",
        "/api/auth/refresh",
    };

    private readonly RequestDelegate _next;
    private readonly AgentSettings _settings;
    private readonly ILogger<AgentAuthMiddleware> _logger;
    private readonly JwtSecurityTokenHandler _handler = new();

    public AgentAuthMiddleware(RequestDelegate next, AgentSettings settings, ILogger<AgentAuthMiddleware> logger)
    {
        _next = next;
        _settings = settings;
        _logger = logger;
    }

    public async Task InvokeAsync(HttpContext context)
    {
        if (_publicPaths.Contains(context.Request.Path))
        {
            await _next(context);
            return;
        }

        var agentSecret = context.Request.Headers["X-Agent-Secret"].ToString();
        if (!string.IsNullOrEmpty(agentSecret))
        {
            if (!string.Equals(agentSecret, _settings.AgentSharedSecret, StringComparison.Ordinal))
            {
                await Reject(context, 401, "Invalid agent secret");
                return;
            }

            var email = context.Request.Headers["X-User-Email"].ToString();
            var role = context.Request.Headers["X-User-Role"].ToString();
            var sessionId = context.Request.Headers["X-Session-Id"].ToString();
            using var scope = RequestContext.Scope(email, role, sessionId);
            await _next(context);
            return;
        }

        var authHeader = context.Request.Headers.Authorization.ToString();
        if (!authHeader.StartsWith("Bearer ", StringComparison.Ordinal))
        {
            await Reject(context, 401, "Missing bearer token");
            return;
        }

        var token = authHeader["Bearer ".Length..];
        var validation = new TokenValidationParameters
        {
            ValidateIssuer = false,
            ValidateAudience = false,
            ValidateLifetime = true,
            ValidateIssuerSigningKey = true,
            IssuerSigningKey = new SymmetricSecurityKey(DeriveKeyBytes(_settings.JwtSecret)),
            ClockSkew = TimeSpan.FromMinutes(1),
        };

        try
        {
            var principal = _handler.ValidateToken(token, validation, out _);
            var email = principal.FindFirst("email")?.Value ?? principal.FindFirst("sub")?.Value ?? "";
            var role = principal.FindFirst("role")?.Value ?? "customer";
            var sessionId = context.Request.Headers["X-Session-Id"].ToString();
            using var scope = RequestContext.Scope(email, role, sessionId);
            await _next(context);
        }
        catch (SecurityTokenException ex)
        {
            _logger.LogWarning("jwt.invalid message={Message}", ex.Message);
            await Reject(context, 401, "Invalid token");
        }
    }

    private static async Task Reject(HttpContext context, int status, string detail)
    {
        context.Response.StatusCode = status;
        context.Response.ContentType = "application/json";
        await context.Response.WriteAsync(JsonSerializer.Serialize(new { detail }));
    }

    /// <summary>Matches the same derivation used by <c>JwtTokenService.DeriveKeyBytes</c>.</summary>
    private static byte[] DeriveKeyBytes(string secret)
    {
        var raw = Encoding.UTF8.GetBytes(secret);
        return raw.Length >= 32 ? raw : SHA256.HashData(raw);
    }
}

public static class AgentAuthMiddlewareExtensions
{
    public static IApplicationBuilder UseAgentAuth(this IApplicationBuilder app) =>
        app.UseMiddleware<AgentAuthMiddleware>();
}
