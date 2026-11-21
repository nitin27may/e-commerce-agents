using ECommerceAgents.Shared.Logging;
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

    /// <summary>
    /// The specialist's registry name: the audience a user-context token must be minted for (B-14). Empty for the
    /// orchestrator, which never receives one.
    /// </summary>
    private readonly string _agentName;

    private readonly byte[] _identityKey;

    public AgentAuthMiddleware(
        RequestDelegate next,
        AgentSettings settings,
        ILogger<AgentAuthMiddleware> logger,
        JwtTokenService jwtTokenService,
        JwksKeyProvider jwksKeyProvider,
        bool isOrchestrator,
        string agentName = ""
    )
    {
        _next = next;
        _settings = settings;
        _logger = logger;
        _jwtTokenService = jwtTokenService;
        _jwksKeyProvider = jwksKeyProvider;
        _isOrchestrator = isOrchestrator;
        _agentName = agentName;
        _identityKey = UserContextToken.ConfiguredKey(settings);
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
                var signingKeys = await _jwksKeyProvider.GetSigningKeysForTokenAsync(token, context.RequestAborted);
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
                var signingKeys = await _jwksKeyProvider.GetSigningKeysForTokenAsync(token, context.RequestAborted);
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
            _logger.LogWarning("jwt.invalid message={Message}", LogSafe.Clean(ex.Message));
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
    /// Resolve which user the inter-agent caller speaks for, flag spoofing, and return the identity — or <c>null</c>
    /// (with a rejection reason) if the request must be refused. Shared by both inter-agent credential paths (shared
    /// secret in local mode, service token in oauth mode) since forwarded-identity handling is identical either way.
    /// Missing headers (system/health flows) default to "system".
    /// </summary>
    /// <remarks>
    /// Where the identity comes from is <see cref="AgentSettings.A2aIdentity"/> (B-14, <see cref="UserContextToken"/>):
    /// <c>headers</c> the forwarded <c>X-User-*</c> headers, trusted on the strength of the inter-agent credential (the
    /// original behaviour; any <c>X-User-Context</c> is ignored). <c>both</c> a signed token when one is present, else
    /// the headers; a token that does not verify is refused, never ignored (a caller that sent one meant it), and a
    /// header beside a valid token must say the same thing, so a forged <c>X-User-Email</c> next to someone else's token
    /// is refused. <c>token</c> only a verified token; the headers are not read for identity at all.
    /// </remarks>
    private (string Email, string Role, string SessionId)? ResolveForwardedIdentity(
        HttpContext context,
        out string? rejectionReason
    )
    {
        var mode = _settings.A2aIdentity;
        var token = mode == "headers" ? string.Empty : context.Request.Headers[UserContextToken.HeaderName].ToString();
        var headerEmail = context.Request.Headers.TryGetValue("X-User-Email", out var e) ? e.ToString() : null;
        var headerRole = context.Request.Headers.TryGetValue("X-User-Role", out var r) ? r.ToString() : null;
        var headerSession = context.Request.Headers["X-Session-Id"].ToString();

        string email, role, sessionId;
        if (token.Length > 0)
        {
            UserContext verified;
            try
            {
                verified = UserContextToken.Verify(token, _agentName, _identityKey);
            }
            catch (IdentityTokenException ex)
            {
                _logger.LogWarning(
                    "security.user_context_rejected agent={Agent} reason={Reason}", LogSafe.Clean(_agentName), ex.Reason);
                rejectionReason = "Invalid user context";
                return null;
            }

            if ((headerEmail is not null && headerEmail != verified.Email)
                || (headerRole is not null && headerRole != verified.Role)
                || (headerSession.Length > 0 && headerSession != verified.SessionId))
            {
                _logger.LogWarning("security.user_context_mismatch agent={Agent}", LogSafe.Clean(_agentName));
                rejectionReason = "Identity does not match the user context";
                return null;
            }

            (email, role, sessionId) = (verified.Email, verified.Role, verified.SessionId);
        }
        else if (mode == "token")
        {
            _logger.LogWarning("security.user_context_missing agent={Agent}", LogSafe.Clean(_agentName));
            rejectionReason = "User context required";
            return null;
        }
        else
        {
            email = string.IsNullOrEmpty(headerEmail) ? "system" : headerEmail;
            role = string.IsNullOrEmpty(headerRole) ? "system" : headerRole;
            sessionId = headerSession;
        }

        var anomaly = IdentityAnomaly(email, role);
        if (anomaly is not null)
        {
            _logger.LogWarning(
                "security.identity_spoof_suspected reason={Reason} email={Email} role={Role}",
                LogSafe.Clean(anomaly),
                LogSafe.Clean(email),
                LogSafe.Clean(role)
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
    /// <paramref name="agentName"/>: a specialist's registry name, the audience its user-context tokens (B-14) must be
    /// minted for. <paramref name="isOrchestrator"/>: true for the orchestrator (its own
    /// user-facing Bearer JWTs validate against
    /// <see cref="AgentSettings.AuthOrchAudience"/>), false — the default —
    /// for a specialist (inter-agent Bearer service tokens validate against
    /// <see cref="AgentSettings.AuthAgentAudience"/> in oauth mode).
    /// </summary>
    public static IApplicationBuilder UseAgentAuth(
        this IApplicationBuilder app, bool isOrchestrator = false, string agentName = "") =>
        app.UseMiddleware<AgentAuthMiddleware>(isOrchestrator, agentName);
}
