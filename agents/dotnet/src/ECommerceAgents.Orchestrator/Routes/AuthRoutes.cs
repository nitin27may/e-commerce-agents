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
    // `Name` is what the web client and the Python stack send; `FullName` is the older .NET-only
    // spelling, still accepted. `Role` is accepted for wire compatibility and ignored: signup
    // always creates a customer (it used to honour it, so anyone could sign up as admin).
    public sealed record SignupRequest(string? Email, string? Password, string? Name = null, string? FullName = null, string? Role = null);

    // Same loose rule as Python's _is_plausible_email: exactly one "@", no whitespace, a dot
    // inside the domain. Plain checks rather than a Regex: the equivalent pattern backtracks
    // polynomially on crafted input.
    internal static bool IsPlausibleEmail(string email)
    {
        foreach (var ch in email)
        {
            if (char.IsWhiteSpace(ch)) return false;
        }

        var at = email.IndexOf('@');
        if (at <= 0 || at != email.LastIndexOf('@')) return false;

        var domain = email.AsSpan(at + 1);
        // A dot with at least one character on each side.
        return domain.Length >= 3 && domain[1..^1].Contains('.');
    }

    private const int MaxPasswordBytes = 72; // bcrypt's limit

    // Email lookups for credentials typed by a person: accounts created by older Python builds kept
    // the address as typed (mixed case) and migration 0004 leaves a row alone when lower-casing it
    // would collide, so match case-insensitively, preferring the exact spelling typed, then the
    // oldest row. @typed is the address as typed.
    private const string UserByTypedEmail =
        "WHERE lower(email) = lower(@typed) ORDER BY (email = @typed) DESC, created_at LIMIT 1";

    private static async Task<string> NewRefreshTokenAsync(
        Npgsql.NpgsqlConnection conn, JwtTokenService jwt, AgentSettings settings, string email, string role
    ) => settings.AuthRefreshRotation
        ? (await RefreshTokenStore.IssueAsync(conn, jwt, email, role, settings.AuthRefreshFamilyMaxDays)).Token
        : jwt.IssueRefreshToken(email, role);
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
    /// The address-only ceiling (default 300 per 60 s) across signup, login and refresh - its own
    /// bucket, so a flood neither spends nor hides behind the chat budget. Deliberately loose:
    /// behind the default web proxy every browser user shares one address, so the tight limits are
    /// per identity (<see cref="AccountLimitAsync"/>, <see cref="UserLimitAsync"/>). Fails open
    /// (error log) when Redis is down, like the chat limiter.
    /// </summary>
    internal sealed class AuthRateLimitFilter(SlidingWindowRateLimiter limiter, AgentSettings settings) : IEndpointFilter
    {
        public async ValueTask<object?> InvokeAsync(EndpointFilterInvocationContext context, EndpointFilterDelegate next)
        {
            var http = context.HttpContext;
            var ip = ClientAddress.Resolve(
                http.Request.Headers["X-Forwarded-For"].FirstOrDefault(),
                http.Connection.RemoteIpAddress,
                settings);

            if (!await limiter.TryAcquireAsync(
                    SlidingWindowRateLimiter.AuthKeyFor(ip),
                    settings.RateLimitAuthIpMaxRequests,
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

    private static readonly object TooManyBody = new { detail = "Too many authentication requests - try again shortly." };

    private static IResult TooMany() => Results.Json(TooManyBody, statusCode: StatusCodes.Status429TooManyRequests);

    /// <summary>Tight login / signup budget per (client address, target email); null when allowed.</summary>
    private static async Task<IResult?> AccountLimitAsync(
        HttpContext http, SlidingWindowRateLimiter limiter, AgentSettings settings, string email)
    {
        var ip = ClientAddress.Resolve(
            http.Request.Headers["X-Forwarded-For"].FirstOrDefault(),
            http.Connection.RemoteIpAddress,
            settings);
        return await limiter.TryAcquireAsync(
            SlidingWindowRateLimiter.AuthAccountKeyFor(ip, email),
            settings.RateLimitAuthMaxRequests,
            settings.RateLimitAuthWindowSeconds,
            http.RequestAborted)
            ? null
            : TooMany();
    }

    /// <summary>Tight refresh budget per user; null when allowed. <paramref name="subject"/> must be verified.</summary>
    private static async Task<IResult?> UserLimitAsync(
        HttpContext http, SlidingWindowRateLimiter limiter, AgentSettings settings, string subject)
        => await limiter.TryAcquireAsync(
            SlidingWindowRateLimiter.AuthUserKeyFor(subject),
            settings.RateLimitAuthMaxRequests,
            settings.RateLimitAuthWindowSeconds,
            http.RequestAborted)
            ? null
            : TooMany();

    private static async Task<IResult> Signup(
        [FromBody] SignupRequest request,
        HttpContext http,
        SlidingWindowRateLimiter limiter,
        DatabasePool pool,
        JwtTokenService jwt,
        AgentSettings settings
    )
    {
        // Same checks, order and messages as Python's _validate_signup (400 + {"detail": msg}).
        var email = (request.Email ?? "").Trim().ToLowerInvariant();
        if (email.Length > 254 || !IsPlausibleEmail(email))
        {
            return Results.BadRequest(new { detail = "Invalid email address" });
        }

        var password = request.Password ?? "";
        var passwordBytes = System.Text.Encoding.UTF8.GetByteCount(password);
        if (passwordBytes < 8 || passwordBytes > MaxPasswordBytes)
        {
            return Results.BadRequest(new { detail = "Password must be between 8 and 72 bytes long" });
        }

        var name = (request.Name ?? request.FullName ?? "").Trim();
        var nameLength = name.EnumerateRunes().Count(); // code points, like Python's len(str)
        if (nameLength < 1 || nameLength > 100)
        {
            return Results.BadRequest(new { detail = "Name must be between 1 and 100 characters" });
        }

        if (await AccountLimitAsync(http, limiter, settings, email) is { } limited)
        {
            return limited;
        }

        const string role = "customer";
        var passwordHash = BCrypt.Net.BCrypt.HashPassword(password);

        await using var conn = await pool.OpenAsync();
        var existing = await conn.ExecuteScalarAsync<int>(
            "SELECT COUNT(1) FROM users WHERE lower(email) = @email",
            new { email }
        );
        if (existing > 0)
        {
            return Results.Conflict(new { detail = "email already registered" });
        }

        var inserted = await conn.QuerySingleAsync(
            @"INSERT INTO users (email, password_hash, name, role)
              VALUES (@email, @passwordHash, @name, @role)
              RETURNING id, loyalty_tier, total_spend",
            new { email, passwordHash, name, role }
        );

        return Results.Ok(new TokenResponse(
            AccessToken: jwt.IssueAccessToken(email, role),
            RefreshToken: await NewRefreshTokenAsync(conn, jwt, settings, email, role),
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
        HttpContext http,
        SlidingWindowRateLimiter limiter,
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

        var typed = request.Email.Trim();
        var email = typed.ToLowerInvariant();
        if (await AccountLimitAsync(http, limiter, settings, email) is { } limited)
        {
            return limited;
        }

        if (settings.AuthMode == "oauth")
        {
            AuthServerTokenResponse token;
            try
            {
                token = await authServer.RequestTokenAsync(
                    "password",
                    new Dictionary<string, string>
                    {
                        // The address as typed: the auth-server matches case-insensitively and prefers an exact spelling.
                        ["username"] = typed,
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
                $"SELECT id, email, name, role, loyalty_tier, total_spend FROM users {UserByTypedEmail}",
                new { typed }
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
            $@"SELECT id, email, password_hash, name, role, loyalty_tier, total_spend, is_active
              FROM users {UserByTypedEmail}",
            new { typed }
        );
        // An over-long attempt is a mismatch (bcrypt reads 72 bytes), never an error - same as Python's verify_password.
        if (user is null
            || System.Text.Encoding.UTF8.GetByteCount(request.Password) > MaxPasswordBytes
            || !BCrypt.Net.BCrypt.Verify(request.Password, (string)user.password_hash))
        {
            return Results.Unauthorized();
        }

        if (!(bool)user.is_active)
        {
            return Results.Json(new { detail = "Account is deactivated" }, statusCode: 403);
        }

        var role = (string)user.role;
        // The address as stored, not as normalised above: a row migration 0004 left mixed-case
        // must keep its own spelling in the token or every later lookup by it would miss.
        var storedEmail = (string)user.email;
        return Results.Ok(new TokenResponse(
            AccessToken: jwt.IssueAccessToken(storedEmail, role),
            RefreshToken: await NewRefreshTokenAsync(conn, jwt, settings, storedEmail, role),
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
        HttpContext http,
        SlidingWindowRateLimiter limiter,
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
            // An opaque token cannot be verified here, so its own digest is the bucket key (one
            // session's budget; a made-up token only spends a bucket nobody else uses).
            if (await UserLimitAsync(http, limiter, settings, request.RefreshToken) is { } oauthLimited)
            {
                return oauthLimited;
            }

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
            // Python-issued refresh tokens carry only `sub`; ours carry both.
            var email = principal.FindFirst("email")?.Value ?? principal.FindFirst("sub")?.Value ?? "";

            // With rotation on, only a real refresh token is acceptable (an access token has no
            // jti row either, but say so up front); flag off keeps the legacy leniency.
            if (settings.AuthRefreshRotation && principal.FindFirst("type")?.Value != "refresh")
            {
                return Results.Unauthorized();
            }

            // Keyed by the verified subject: one user's refreshes never spend another's budget. An
            // unverifiable or expired token threw above and is held only by the address ceiling.
            if (await UserLimitAsync(http, limiter, settings, email) is { } limited)
            {
                return limited;
            }

            // Mirrors Python: re-check the DB (not just the token's claims) so a
            // deactivated or deleted account can't refresh a still-valid token.
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

            var role = (string)user.role;
            var accessToken = jwt.IssueAccessToken(email, role);
            if (!settings.AuthRefreshRotation)
            {
                return Results.Ok(new { access_token = accessToken });
            }

            // Rotation: consume this token, hand back its successor. A token already consumed
            // (or one that predates `jti`) is rejected; reuse revokes the whole family.
            var (next, _) = await RefreshTokenStore.RotateAsync(
                pool, jwt, principal.FindFirst("jti")?.Value, email, role, settings.AuthRefreshFamilyMaxDays);
            if (next is null)
            {
                return Results.Json(new { detail = "Invalid refresh token" }, statusCode: StatusCodes.Status401Unauthorized);
            }

            return Results.Ok(new { access_token = accessToken, refresh_token = next.Token });
        }
        catch
        {
            return Results.Unauthorized();
        }
    }
}
