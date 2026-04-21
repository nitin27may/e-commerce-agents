using Dapper;
using ECommerceAgents.Shared.Auth;
using ECommerceAgents.Shared.Data;
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
public static class AuthRoutes
{
    public sealed record SignupRequest(string Email, string Password, string? FullName, string? Role);
    public sealed record LoginRequest(string Email, string Password);
    public sealed record RefreshRequest(string RefreshToken);

    public sealed record TokenResponse(string AccessToken, string RefreshToken, string Email, string Role);

    public static IEndpointRouteBuilder MapAuthRoutes(this IEndpointRouteBuilder routes)
    {
        routes.MapPost("/api/auth/signup", Signup);
        routes.MapPost("/api/auth/login", Login);
        routes.MapPost("/api/auth/refresh", Refresh);
        return routes;
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

        await conn.ExecuteAsync(
            @"INSERT INTO users (email, password_hash, full_name, role)
              VALUES (@email, @passwordHash, @fullName, @role)",
            new { email, passwordHash, fullName = request.FullName, role }
        );

        return Results.Ok(new TokenResponse(
            AccessToken: jwt.IssueAccessToken(email, role),
            RefreshToken: jwt.IssueRefreshToken(email, role),
            Email: email,
            Role: role
        ));
    }

    private static async Task<IResult> Login(
        [FromBody] LoginRequest request,
        DatabasePool pool,
        JwtTokenService jwt
    )
    {
        if (string.IsNullOrWhiteSpace(request.Email) || string.IsNullOrWhiteSpace(request.Password))
        {
            return Results.BadRequest(new { detail = "email and password are required" });
        }

        var email = request.Email.Trim().ToLowerInvariant();
        await using var conn = await pool.OpenAsync();
        var user = await conn.QueryFirstOrDefaultAsync(
            "SELECT email, password_hash, role FROM users WHERE email = @email",
            new { email }
        );
        if (user is null || !BCrypt.Net.BCrypt.Verify(request.Password, (string)user.password_hash))
        {
            return Results.Unauthorized();
        }

        var role = (string)user.role;
        return Results.Ok(new TokenResponse(
            AccessToken: jwt.IssueAccessToken(email, role),
            RefreshToken: jwt.IssueRefreshToken(email, role),
            Email: email,
            Role: role
        ));
    }

    private static IResult Refresh([FromBody] RefreshRequest request, JwtTokenService jwt)
    {
        if (string.IsNullOrWhiteSpace(request.RefreshToken))
        {
            return Results.BadRequest(new { detail = "refresh_token is required" });
        }

        try
        {
            var principal = jwt.Validate(request.RefreshToken);
            var email = principal.FindFirst("email")?.Value ?? "";
            var role = principal.FindFirst("role")?.Value ?? "customer";
            return Results.Ok(new TokenResponse(
                AccessToken: jwt.IssueAccessToken(email, role),
                RefreshToken: jwt.IssueRefreshToken(email, role),
                Email: email,
                Role: role
            ));
        }
        catch
        {
            return Results.Unauthorized();
        }
    }
}
