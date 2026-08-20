using ECommerceAgents.Shared.Auth;
using ECommerceAgents.Shared.Configuration;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;
using Microsoft.IdentityModel.Tokens;

namespace ECommerceAgents.Mcp;

/// <summary>
/// Health checks and OAuth 2.1 resource-server support for the real MCP
/// server mapped at <c>/mcp</c> by <see cref="McpTools"/> +
/// <c>ModelContextProtocol.AspNetCore</c>'s <c>MapMcp()</c> (see
/// <c>Program.cs</c>). The actual tool dispatch/discovery is handled by the
/// MCP protocol itself (JSON-RPC over streamable HTTP) — this class only
/// covers what sits alongside it: liveness endpoints and the bearer-token
/// gate.
/// </summary>
/// <remarks>
/// OAuth 2.1 resource-server mode (optional — <see cref="AgentSettings.McpAuthEnabled"/>,
/// off by default): when enabled, <c>UseMcpAuthGate</c> requires a valid
/// RS256 Bearer token (aud/scope from <see cref="AgentSettings"/>, validated
/// the same way as the Phase B/C orchestrator/agent paths via
/// <see cref="JwtTokenService.ValidateOAuth"/> + <see cref="JwksKeyProvider"/>
/// — no <c>AddMicrosoftIdentityWebApi</c>) on every request under the MCP
/// route prefix. A missing/invalid token gets a spec-shaped 401 +
/// <c>WWW-Authenticate</c> header; the manifest and health routes stay
/// unauthenticated either way, matching the Python SDK's own behavior of
/// leaving discovery/health surfaces public.
/// </remarks>
public static class McpEndpoints
{
    public const string McpRoutePrefix = "/mcp";

    public static IEndpointRouteBuilder MapMcpHealthEndpoints(this IEndpointRouteBuilder routes)
    {
        routes.MapGet("/", () =>
            Results.Ok(new { service = "mcp-inventory", healthy = true })
        );
        routes.MapGet("/health", () => Results.Ok(new { healthy = true }));
        routes.MapGet("/.well-known/oauth-protected-resource", ProtectedResourceMetadata);
        return routes;
    }

    /// <summary>
    /// Gates every request under <see cref="McpRoutePrefix"/> behind bearer-token
    /// validation when <see cref="AgentSettings.McpAuthEnabled"/> is on. Must be
    /// registered before <c>app.MapMcp()</c> in the middleware pipeline.
    /// </summary>
    public static IApplicationBuilder UseMcpAuthGate(this IApplicationBuilder app) =>
        app.Use(async (http, next) =>
        {
            var settings = http.RequestServices.GetRequiredService<AgentSettings>();
            if (!settings.McpAuthEnabled || !http.Request.Path.StartsWithSegments(McpRoutePrefix))
            {
                await next(http);
                return;
            }

            var jwt = http.RequestServices.GetRequiredService<JwtTokenService>();
            var jwks = http.RequestServices.GetRequiredService<JwksKeyProvider>();

            var authHeader = http.Request.Headers.Authorization.ToString();
            if (!authHeader.StartsWith("Bearer ", StringComparison.Ordinal))
            {
                await Unauthorized(http, settings, "invalid_token", "Authentication required");
                return;
            }

            var token = authHeader["Bearer ".Length..];
            try
            {
                var signingKeys = await jwks.GetSigningKeysAsync(http.RequestAborted);
                jwt.ValidateOAuth(token, signingKeys, settings.McpAudience, requiredScope: settings.McpRequiredScope);
                await next(http);
            }
            catch (SecurityTokenException)
            {
                await Unauthorized(http, settings, "invalid_token", "Invalid or expired token");
            }
        });

    private static async Task Unauthorized(HttpContext http, AgentSettings settings, string error, string description)
    {
        var resourceMetadataUrl = $"{http.Request.Scheme}://{http.Request.Host}/.well-known/oauth-protected-resource";
        http.Response.Headers.Append(
            "WWW-Authenticate",
            $"Bearer error=\"{error}\", error_description=\"{description}\", resource_metadata=\"{resourceMetadataUrl}\""
        );
        http.Response.StatusCode = 401;
        await http.Response.WriteAsJsonAsync(new { error, error_description = description });
    }

    private static IResult ProtectedResourceMetadata(AgentSettings settings)
    {
        // Publish the issuer exactly as configured — the same raw value used by
        // JwtTokenService's ValidIssuer check and by the tokens the auth server
        // mints (see agents/python/auth_server: iss / token_endpoint use the raw
        // AUTH_SERVER_ISSUER with no trailing-slash rewrite). A rewritten value
        // here would advertise an issuer that doesn't match what tokens actually
        // carry.
        return Results.Ok(new
        {
            resource = settings.McpResourceUrl,
            authorization_servers = new[] { settings.AuthServerIssuer },
            scopes_supported = new[] { settings.McpRequiredScope },
            bearer_methods_supported = new[] { "header" },
        });
    }
}
