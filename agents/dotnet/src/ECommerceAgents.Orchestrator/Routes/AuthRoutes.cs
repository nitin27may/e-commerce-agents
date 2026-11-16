using Dapper;
using ECommerceAgents.Shared.Auth;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.RateLimiting;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;

namespace ECommerceAgents.Orchestrator.Routes;

/// <summary>
/// Mirrors the signup / login / refresh endpoints from Python's
/// <c>orchestrator/routes.py</c>. Passwords are hashed with BCrypt —
/// identical scheme to the Python bcrypt calls — so rows created by
/// either backend authenticate from the other.
/// </summary>
/// <remarks>
/// In <c>AuthMode=oauth</c>, Login and Refresh broker to the self-hosted
/// auth-server via <see cref="AuthServerClient"/> instead of issuing HS256
/// tokens locally — the AS is the sole authority on credentials (it
/// re-verifies the same bcrypt hash), so this does not duplicate the
/// password check. Signup is untouched in both modes: new users are always
/// created locally and issued a local token, matching Python's behavior.
/// </remarks>
public static class AuthRoutes
{
    public sealed record SignupRequest(string Email, string Password, string? FullName, string? Role);
    public sealed record LoginRequest(string Email, string Password);
    public sealed record RefreshRequest(string RefreshToken);

    // Mirrors Python's AuthResponse: access_token/refresh_token + a nested `user` object
    // (id, email, name, role, loyalty_tier, total_spend). web/src/lib/auth-context.tsx reads
    // result.user directly — a flat response leaves `user` undefined and login silently
    // never completes (no redirect), even though the tokens themselves are valid.
    public sealed record UserInfo(string Id, string Email, string Name, string Role, string? LoyaltyTier, decimal TotalSpend);
    public sealed record TokenResponse(string AccessToken, string RefreshToken, UserInfo User);

    public static IEndpointRouteBuilder MapAuthRoutes(this IEndpointRouteBuilder routes)
    {
        routes.MapPost("/api/auth/signup", Signup).AddEndpointFilter<AuthRateLimitFilter>();
        routes.MapPost("/api/auth/login", Login).AddEndpointFilter<AuthRateLimitFilter>();
        routes.MapPost("/api/auth/refresh", Refresh).AddEndpointFilter<AuthRateLimitFilter>();
        return routes;
    }

    /// <summary>
    /// 10 requests per 60 s per client address across signup, login and refresh - its own
    /// bucket, so a credential-guessing loop neither spends nor hides behind the chat budget.
    /// Fails open (error log) when Redis is down, like the chat limiter.
    /// </summary>
    internal sealed class AuthRateLimitFilter(SlidingWindowRateLimiter limiter, AgentSettings settings) : IEndpointFilter
    {
        public async ValueTask<object?> InvokeAsync(EndpointFilterInvocationContext context, EndpointFilterDelegate next)
        {
            var http = context.HttpContext;
            var ip = ClientAddress.Resolve(
                http.Request.Headers["X-Forwarded-For"].FirstOrDefault(),
                http.Connection.RemoteIpAddress,
                settings.RateLimitTrustedProxyHops);

            if (!await limiter.TryAcquireAsync(
                    SlidingWindowRateLimiter.AuthKeyFor(ip),
                    settings.RateLimitAuthMaxRequests,
                    settings.RateLimitAuthWindowSeconds,
                    http.RequestAborted))
            {
                return Results.Json(
                    new { detail = "Too many authentication requests - try again shortly." },
                    statusCode: StatusCodes.Status429TooManyRequests
                );
            }

            return await next(context);
        }
    }

    private static async Task<IResult> Signup(
        [FromBody] SignupRequest request,
        DatabasePool pool,
        JwtTokenService jwt
    )
    {
        if (string.IsNullOrWhiteSpace(request.Email) || string.IsNullOrWhiteSpace(request.Password))
        {
            return Results.BadRequest(new { detail = "email and password are required" });
        }

        var email = request.Email.Trim().ToLowerInvariant();
        var role = string.IsNullOrWhiteSpace(request.Role) ? "customer" : request.Role.Trim().ToLowerInvariant();
        var passwordHash = BCrypt.Net.BCrypt.HashPassword(request.Password);

        await using var conn = await pool.OpenAsync();
        var existing = await conn.ExecuteScalarAsync<int>(
            "SELECT COUNT(1) FROM users WHERE email = @email",
            new { email }
        );
        if (existing > 0)
        {
            return Results.Conflict(new { detail = "email already registered" });
        }

        var name = request.FullName ?? email;
        var inserted = await conn.QuerySingleAsync(
            @"INSERT INTO users (email, password_hash, name, role)
              VALUES (@email, @passwordHash, @name, @role)
              RETURNING id, loyalty_tier, total_spend",
            new { email, passwordHash, name, role }
        );

        return Results.Ok(new TokenResponse(
            AccessToken: jwt.IssueAccessToken(email, role),
            RefreshToken: jwt.IssueRefreshToken(email, role),
            User: new UserInfo(
                Id: inserted.id.ToString(),
                Email: email,
                Name: name,
                Role: role,
                LoyaltyTier: inserted.loyalty_tier,
                TotalSpend: inserted.total_spend
            )
        ));
    }

    private static async Task<IResult> Login(
        [FromBody] LoginRequest request,
        DatabasePool pool,
        JwtTokenService jwt,
        AgentSettings settings,
        AuthServerClient authServer
    )
    {
        if (string.IsNullOrWhiteSpace(request.Email) || string.IsNullOrWhiteSpace(request.Password))
        {
            return Results.BadRequest(new { detail = "email and password are required" });
        }

        var email = request.Email.Trim().ToLowerInvariant();

        if (settings.AuthMode == "oauth")
        {
            AuthServerTokenResponse token;
            try
            {
                token = await authServer.RequestTokenAsync(
                    "password",
                    new Dictionary<string, string>
                    {
                        ["username"] = email,
                        ["password"] = request.Password,
                        ["scope"] = "api:chat",
                    }
                );
            }
            catch (HttpRequestException)
            {
                return Results.Unauthorized();
            }

            await using var oauthConn = await pool.OpenAsync();
            var oauthUser = await oauthConn.QueryFirstOrDefaultAsync(
                "SELECT id, email, name, role, loyalty_tier, total_spend FROM users WHERE email = @email",
                new { email }
            );
            // The AS validated these credentials against the same `users`
            // table; a missing row here would mean it vanished between reads.
            if (oauthUser is null)
            {
                return Results.Unauthorized();
            }

            return Results.Ok(new TokenResponse(
                AccessToken: token.AccessToken,
                RefreshToken: token.RefreshToken ?? "",
                User: new UserInfo(
                    Id: oauthUser.id.ToString(),
                    Email: (string)oauthUser.email,
                    Name: (string)oauthUser.name,
                    Role: (string)oauthUser.role,
                    LoyaltyTier: oauthUser.loyalty_tier,
                    TotalSpend: oauthUser.total_spend
                )
            ));
        }

        await using var conn = await pool.OpenAsync();
        var user = await conn.QueryFirstOrDefaultAsync(
            @"SELECT id, email, password_hash, name, role, loyalty_tier, total_spend, is_active
              FROM users WHERE email = @email",
            new { email }
        );
        if (user is null || !BCrypt.Net.BCrypt.Verify(request.Password, (string)user.password_hash))
        {
            return Results.Unauthorized();
        }

        if (!(bool)user.is_active)
        {
            return Results.Json(new { detail = "Account is deactivated" }, statusCode: 403);
        }

        var role = (string)user.role;
        return Results.Ok(new TokenResponse(
            AccessToken: jwt.IssueAccessToken(email, role),
            RefreshToken: jwt.IssueRefreshToken(email, role),
            User: new UserInfo(
                Id: user.id.ToString(),
                Email: (string)user.email,
                Name: (string)user.name,
                Role: role,
                LoyaltyTier: user.loyalty_tier,
                TotalSpend: user.total_spend
            )
        ));
    }

    private static async Task<IResult> Refresh(
        [FromBody] RefreshRequest request,
        DatabasePool pool,
        JwtTokenService jwt,
        AgentSettings settings,
        AuthServerClient authServer
    )
    {
        if (string.IsNullOrWhiteSpace(request.RefreshToken))
        {
            return Results.BadRequest(new { detail = "refresh_token is required" });
        }

        if (settings.AuthMode == "oauth")
        {
            try
            {
                var token = await authServer.RequestTokenAsync(
                    "refresh_token",
                    new Dictionary<string, string> { ["refresh_token"] = request.RefreshToken }
                );
                return Results.Ok(new { access_token = token.AccessToken });
            }
            catch (HttpRequestException)
            {
                return Results.Unauthorized();
            }
        }

        try
        {
            var principal = jwt.Validate(request.RefreshToken);
            var email = principal.FindFirst("email")?.Value ?? "";

            // Mirrors Python: re-check the DB (not just the token's claims) so a
            // deactivated or deleted account can't refresh a still-valid token,
            // and issue only a new access_token — the refresh token itself isn't
            // rotated (matches the existing single-refresh-token contract).
            await using var conn = await pool.OpenAsync();
            var user = await conn.QueryFirstOrDefaultAsync(
                "SELECT role, is_active FROM users WHERE email = @email",
                new { email }
            );
            if (user is null)
            {
                return Results.Unauthorized();
            }
            if (!(bool)user.is_active)
            {
                return Results.Json(new { detail = "Account is deactivated" }, statusCode: 403);
            }

            var accessToken = jwt.IssueAccessToken(email, (string)user.role);
            return Results.Ok(new { access_token = accessToken });
        }
        catch
        {
            return Results.Unauthorized();
        }
    }
}
