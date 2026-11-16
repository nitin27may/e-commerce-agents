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
/// <item>External: <c>Authorization: Bearer &lt;JWT&gt;</c> — validates signature and extracts the <c>email</c> + <c>role</c> claims. In <c>oauth</c> mode this is only meaningful on the orchestrator (<paramref name="isOrchestrator"/>=true) — specialists never receive genuine end-user tokens directly.</item>
/// <item>Inter-agent (A2A): <c>local</c> mode uses <c>X-Agent-Secret</c> equal to <see cref="AgentSettings.AgentSharedSecret"/>; <c>oauth</c> mode uses an AS-issued RS256 service token instead (<c>aud=ecommerce-agents</c>, <c>scope=agent:invoke</c>) — the shared secret is rejected outright. Either way the caller provides the user via <c>X-User-Email</c> / <c>X-User-Role</c> / <c>X-Session-Id</c> headers; missing headers (system/health flows) default to <c>role=system</c>.</item>
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
        "/ready",
        "/.well-known/agent-card.json",
        "/api/auth/signup",
        "/api/auth/login",
        "/api/auth/refresh",
    };

    /// <summary>
    /// Exact-match paths that mirror Python's <c>optional_auth</c> dependency
    /// (<c>orchestrator/routes.py::optional_auth</c>) — the public storefront
    /// surface. Unlike <see cref="_publicPaths"/> (never authenticated), these
    /// still authenticate a *present* Bearer token normally; only a *missing*
    /// header is treated as anonymous instead of a 401.
    /// </summary>
    private static readonly HashSet<string> _optionalAuthPaths = new(StringComparer.OrdinalIgnoreCase)
    {
        "/api/chat",
        "/api/chat/stream",
    };

    /// <summary>Prefix-matched alongside <see cref="_optionalAuthPaths"/> — covers both
    /// <c>/api/products</c> and <c>/api/products/{id}</c> without needing route-template
    /// matching.</summary>
    private const string OptionalAuthPathPrefix = "/api/products";

    /// <summary>Roles the platform recognizes. 'system' is the inter-agent sentinel used when a call originates without an end user (internal / health flows). 'support' is the staff role that reviews approvals routed to it; 'power_user' is a seeded customer variant.</summary>
    private static readonly HashSet<string> _allowedRoles = new(StringComparer.OrdinalIgnoreCase)
    {
        "customer",
        "power_user",
        "seller",
        "support",
        "admin",
        "system",
    };

    private readonly RequestDelegate _next;
    private readonly AgentSettings _settings;
    private readonly ILogger<AgentAuthMiddleware> _logger;

    /// <summary>
    /// <c>MapInboundClaims = false</c> — see the identical note on
    /// <see cref="JwtTokenService"/>'s own handler field. Without it,
    /// <c>FindFirst("email")</c>/<c>FindFirst("role")</c> below always
    /// return null and every request silently falls back to the
    /// empty-email/"customer" defaults.
    /// </summary>
    private readonly JwtSecurityTokenHandler _handler = new() { MapInboundClaims = false };
    private readonly JwtTokenService _jwtTokenService;
    private readonly JwksKeyProvider _jwksKeyProvider;

    /// <summary>
    /// True for the orchestrator (validates genuine end-user Bearer tokens
    /// against <see cref="AgentSettings.AuthOrchAudience"/>), false for a
    /// specialist (validates inter-agent service tokens against
    /// <see cref="AgentSettings.AuthAgentAudience"/>). Set at startup via
    /// <see cref="AgentAuthMiddlewareExtensions.UseAgentAuth"/> — unlike
    /// Python, where these are two entirely separate code paths
    /// (<c>orchestrator/routes.py::require_auth</c> vs
    /// <c>shared/auth.py::AgentAuthMiddleware</c>), .NET's orchestrator and
    /// specialists share this one middleware class.
    /// </summary>
    private readonly bool _isOrchestrator;

    public AgentAuthMiddleware(
        RequestDelegate next,
        AgentSettings settings,
        ILogger<AgentAuthMiddleware> logger,
        JwtTokenService jwtTokenService,
        JwksKeyProvider jwksKeyProvider,
        bool isOrchestrator
    )
    {
        _next = next;
        _settings = settings;
        _logger = logger;
        _jwtTokenService = jwtTokenService;
        _jwksKeyProvider = jwksKeyProvider;
        _isOrchestrator = isOrchestrator;
    }

    public async Task InvokeAsync(HttpContext context)
    {
        if (_publicPaths.Contains(context.Request.Path))
        {
            await _next(context);
            return;
        }

        var agentSecret = context.Request.Headers["X-Agent-Secret"].ToString();

        // oauth mode retires the shared secret entirely — a request bearing
        // it is rejected outright rather than silently falling through to
        // the service-token path below.
        if (_settings.AuthMode == "oauth" && !string.IsNullOrEmpty(agentSecret))
        {
            _logger.LogWarning("auth.denied reason=agent_secret_disabled_in_oauth_mode");
            await Reject(context, 401, "Inter-agent shared secret is disabled in oauth mode");
            return;
        }

        // The orchestrator is the user-facing front door: it authenticates
        // end users by Bearer token only. Nothing legitimate sends it an
        // X-Agent-Secret, and honouring one would let anyone holding the
        // shared secret act as any user via X-User-Email / X-User-Role.
        if (_isOrchestrator && !string.IsNullOrEmpty(agentSecret))
        {
            _logger.LogWarning("auth.denied reason=agent_secret_not_accepted_on_orchestrator");
            await Reject(context, 401, "Inter-agent shared secret is not accepted by the orchestrator");
            return;
        }

        // Inter-agent authentication (local mode): static shared secret.
        if (!string.IsNullOrEmpty(agentSecret))
        {
            if (!SecretsMatch(agentSecret, _settings.AgentSharedSecret))
            {
                await Reject(context, 401, "Invalid agent secret");
                return;
            }

            var identity = ResolveForwardedIdentity(context, out var rejection);
            if (identity is null)
            {
                await Reject(context, 401, rejection!);
                return;
            }
            using var scope = RequestContext.Scope(identity.Value.Email, identity.Value.Role, identity.Value.SessionId);
            await _next(context);
            return;
        }

        var authHeader = context.Request.Headers.Authorization.ToString();
        if (!authHeader.StartsWith("Bearer ", StringComparison.Ordinal))
        {
            // Mirrors Python's optional_auth: a *missing* Authorization header on the
            // public storefront surface (product browse + chat) is anonymous, not a 401.
            // A present-but-invalid token still falls through to the normal validation
            // below and gets rejected — only absence is treated as anonymous.
            if (IsOptionalAuthPath(context.Request.Path))
            {
                var sessionId = context.Request.Headers["X-Session-Id"].ToString();
                using var anonScope = RequestContext.Scope("", "anonymous", sessionId);
                await _next(context);
                return;
            }

            await Reject(context, 401, "Missing bearer token");
            return;
        }

        var token = authHeader["Bearer ".Length..];

        try
        {
            if (_settings.AuthMode == "oauth" && !_isOrchestrator)
            {
                // Inter-agent authentication (oauth mode): AS-issued service
                // token proves the caller is a legitimate first-party agent;
                // the actual end-user identity still travels via the
                // forwarded X-User-* headers, exactly as the shared-secret
                // path above.
                var signingKeys = await _jwksKeyProvider.GetSigningKeysAsync(context.RequestAborted);
                _jwtTokenService.ValidateOAuth(
                    token,
                    signingKeys,
                    _settings.AuthAgentAudience,
                    requiredScope: "agent:invoke"
                );

                var identity = ResolveForwardedIdentity(context, out var rejection);
                if (identity is null)
                {
                    await Reject(context, 401, rejection!);
                    return;
                }
                using var interAgentScope = RequestContext.Scope(
                    identity.Value.Email,
                    identity.Value.Role,
                    identity.Value.SessionId
                );
                await _next(context);
                return;
            }

            System.Security.Claims.ClaimsPrincipal principal;
            if (_settings.AuthMode == "oauth")
            {
                var signingKeys = await _jwksKeyProvider.GetSigningKeysAsync(context.RequestAborted);
                principal = _jwtTokenService.ValidateOAuth(
                    token,
                    signingKeys,
                    _settings.AuthOrchAudience,
                    requiredScope: "api:chat"
                );
            }
            else
            {
                var validation = new TokenValidationParameters
                {
                    ValidateIssuer = false,
                    ValidateAudience = false,
                    ValidateLifetime = true,
                    ValidateIssuerSigningKey = true,
                    IssuerSigningKey = new SymmetricSecurityKey(DeriveKeyBytes(_settings.JwtSecret)),
                    ClockSkew = TimeSpan.FromMinutes(1),
                };
                principal = _handler.ValidateToken(token, validation, out _);
            }

            var email = principal.FindFirst("email")?.Value ?? principal.FindFirst("sub")?.Value ?? "";
            var role = principal.FindFirst("role")?.Value ?? "customer";
            var sessionId = context.Request.Headers["X-Session-Id"].ToString();
            using var scope = RequestContext.Scope(email, role, sessionId);
            await _next(context);
        }
        catch (Exception ex) when (ex is SecurityTokenException or ArgumentException)
        {
            // SecurityTokenMalformedException (thrown for a garbage/non-JWT-shaped
            // token, e.g. one missing the 3-segment structure) does NOT derive from
            // SecurityTokenException in this package version — confirmed via
            // reflection — so a plain `catch (SecurityTokenException)` let malformed
            // tokens crash the request instead of cleanly rejecting it. Widened to
            // catch that + the ArgumentException family so any invalid token input
            // 401s rather than 500s.
            _logger.LogWarning("jwt.invalid message={Message}", ex.Message);
            await Reject(context, 401, "Invalid token");
        }
    }

    /// <summary>
    /// True for the public storefront surface — product browse and chat —
    /// where Python's <c>optional_auth</c> allows anonymous access.
    /// <see cref="OptionalAuthPathPrefix"/> is prefix-matched to cover both
    /// <c>/api/products</c> and <c>/api/products/{id}</c> without needing
    /// route-template resolution inside the middleware.
    /// </summary>
    private static bool IsOptionalAuthPath(PathString path) =>
        _optionalAuthPaths.Contains(path.ToString())
        || path.StartsWithSegments(OptionalAuthPathPrefix, StringComparison.OrdinalIgnoreCase);

    /// <summary>
    /// Read X-User-Email/X-User-Role/X-Session-Id, flag spoofing, and
    /// return the resolved identity — or <c>null</c> (with a rejection
    /// reason) if <see cref="AgentSettings.GuardrailsStrictIdentity"/>
    /// should reject the request. Shared by both inter-agent credential
    /// paths (shared secret in local mode, service token in oauth mode)
    /// since forwarded-identity handling is identical either way. Missing
    /// headers default to "system" (system/health flows).
    /// </summary>
    private (string Email, string Role, string SessionId)? ResolveForwardedIdentity(
        HttpContext context,
        out string? rejectionReason
    )
    {
        var email = context.Request.Headers["X-User-Email"].ToString();
        if (string.IsNullOrEmpty(email))
        {
            email = "system";
        }
        var role = context.Request.Headers["X-User-Role"].ToString();
        if (string.IsNullOrEmpty(role))
        {
            role = "system";
        }
        var sessionId = context.Request.Headers["X-Session-Id"].ToString();

        var anomaly = IdentityAnomaly(email, role);
        if (anomaly is not null)
        {
            _logger.LogWarning(
                "security.identity_spoof_suspected reason={Reason} email={Email} role={Role}",
                anomaly,
                email,
                role
            );
            if (_settings.GuardrailsStrictIdentity)
            {
                rejectionReason = "Invalid forwarded identity";
                return null;
            }
        }

        rejectionReason = null;
        return (email, role, sessionId);
    }

    /// <summary>
    /// The inter-agent credential (shared secret or, in oauth mode, the
    /// service token) authenticates the *caller* (another agent), but the
    /// forwarded X-User-Email/X-User-Role headers are otherwise trusted
    /// blindly. This flags obviously-bad values so they can be logged — and
    /// rejected under <see cref="AgentSettings.GuardrailsStrictIdentity"/> —
    /// instead of silently granting access if a credential ever leaks.
    /// </summary>
    private static string? IdentityAnomaly(string email, string role)
    {
        if (!_allowedRoles.Contains(role))
        {
            return $"unknown_role:{role}";
        }
        if (email != "system" && !email.Contains('@'))
        {
            return "malformed_email";
        }
        return null;
    }

    /// <summary>Constant-time comparison; hashing first makes the inputs equal-length.</summary>
    private static bool SecretsMatch(string provided, string expected) =>
        CryptographicOperations.FixedTimeEquals(
            SHA256.HashData(Encoding.UTF8.GetBytes(provided)),
            SHA256.HashData(Encoding.UTF8.GetBytes(expected ?? string.Empty))
        );

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
    /// <summary>
    /// <paramref name="isOrchestrator"/>: true for the orchestrator (its own
    /// user-facing Bearer JWTs validate against
    /// <see cref="AgentSettings.AuthOrchAudience"/>), false — the default —
    /// for a specialist (inter-agent Bearer service tokens validate against
    /// <see cref="AgentSettings.AuthAgentAudience"/> in oauth mode).
    /// </summary>
    public static IApplicationBuilder UseAgentAuth(this IApplicationBuilder app, bool isOrchestrator = false) =>
        app.UseMiddleware<AgentAuthMiddleware>(isOrchestrator);
}
