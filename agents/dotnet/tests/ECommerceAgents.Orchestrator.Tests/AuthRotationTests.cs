using Dapper;
using ECommerceAgents.Orchestrator.Routes;
using ECommerceAgents.Shared.Auth;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using System.IdentityModel.Tokens.Jwt;
using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Xunit;

namespace ECommerceAgents.Orchestrator.Tests;

/// <summary>
/// A-12 (#102): refresh-token rotation with reuse detection, and signup validation - the .NET
/// mirror of agents/python/tests/test_refresh_rotation.py, with the same messages and wire shape.
/// </summary>
[Collection(nameof(LocalPostgresCollection))]
public sealed class AuthRotationTests(PostgresFixture pg) : IAsyncLifetime
{
    private const string Email = "rotate@example.com";
    private const string Password = "correct horse battery";
    private DatabasePool _pool = null!;

    public async Task InitializeAsync()
    {
        _pool = new DatabasePool(new AgentSettings { DatabaseUrl = pg.ConnectionString });
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            @"TRUNCATE order_status_history, order_items, returns, orders, messages, conversations,
                       warehouse_inventory, warehouses, reviews, products, users RESTART IDENTITY CASCADE");
        await conn.ExecuteAsync(
            "INSERT INTO users (email, password_hash, name, role) VALUES (@Email, @hash, 'Rot Ate', 'customer')",
            new { Email, hash = BCrypt.Net.BCrypt.HashPassword(Password) });
    }

    public async Task DisposeAsync() => await _pool.DisposeAsync();

    private HttpClient Client(bool rotation = true) =>
        OrchestratorTestHost.Create(
            _pool,
            r => r.MapAuthRoutes(),
            new AgentSettings { RateLimitEnabled = false, AuthRefreshRotation = rotation }
        ).CreateClient();

    private static async Task<JsonElement> Json(HttpResponseMessage r) => await r.Content.ReadFromJsonAsync<JsonElement>();

    private static async Task<string> LoginAsync(HttpClient c)
    {
        var r = await c.PostAsJsonAsync("/api/auth/login", new { email = Email, password = Password });
        r.EnsureSuccessStatusCode();
        return (await Json(r)).GetProperty("refresh_token").GetString()!;
    }

    private static Task<HttpResponseMessage> RefreshAsync(HttpClient c, string token) =>
        c.PostAsJsonAsync("/api/auth/refresh", new { refresh_token = token });

    private static string JtiOf(string token) => new JwtSecurityTokenHandler().ReadJwtToken(token).Id;

    // ───────────────────────────── rotation ─────────────────────────────

    [Fact]
    public async Task Refresh_Rotates_AndMarksTheOldTokenReplaced()
    {
        using var c = Client();
        var first = await LoginAsync(c);

        var resp = await RefreshAsync(c, first);
        resp.StatusCode.Should().Be(HttpStatusCode.OK);
        var body = await Json(resp);
        var second = body.GetProperty("refresh_token").GetString()!;
        body.GetProperty("access_token").GetString().Should().NotBeNullOrEmpty();
        second.Should().NotBe(first);

        // The chain keeps working.
        (await RefreshAsync(c, second)).StatusCode.Should().Be(HttpStatusCode.OK);

        await using var conn = await _pool.OpenAsync();
        var old = await conn.QuerySingleAsync("SELECT revoked_at, replaced_by FROM refresh_tokens WHERE jti = @j", new { j = JtiOf(first) });
        ((object?)old.revoked_at).Should().NotBeNull();
        ((string)old.replaced_by).Should().Be(JtiOf(second));
        (await conn.ExecuteScalarAsync<int>("SELECT COUNT(DISTINCT family_id) FROM refresh_tokens")).Should().Be(1);
    }

    [Fact]
    public async Task ReusingARotatedToken_RevokesTheWholeFamily()
    {
        using var c = Client();
        var first = await LoginAsync(c);
        var second = (await Json(await RefreshAsync(c, first))).GetProperty("refresh_token").GetString()!;

        (await RefreshAsync(c, first)).StatusCode.Should().Be(HttpStatusCode.Unauthorized); // the stolen copy
        (await RefreshAsync(c, second)).StatusCode.Should().Be(HttpStatusCode.Unauthorized); // successor is dead too

        await using var conn = await _pool.OpenAsync();
        (await conn.ExecuteScalarAsync<int>("SELECT COUNT(*) FROM refresh_tokens WHERE revoked_at IS NULL")).Should().Be(0);
    }

    [Fact]
    public async Task AnotherFamilySurvivesAReuse()
    {
        using var c = Client();
        var phone = await LoginAsync(c);
        var laptop = await LoginAsync(c);
        await RefreshAsync(c, phone);
        (await RefreshAsync(c, phone)).StatusCode.Should().Be(HttpStatusCode.Unauthorized);
        (await RefreshAsync(c, laptop)).StatusCode.Should().Be(HttpStatusCode.OK);
    }

    [Fact]
    public async Task SimultaneousRefreshesWithOneToken_LetExactlyOneWin()
    {
        using var c = Client();
        var first = await LoginAsync(c);

        var results = await Task.WhenAll(RefreshAsync(c, first), RefreshAsync(c, first));

        results.Select(r => (int)r.StatusCode).OrderBy(x => x).Should().Equal(200, 401);
    }

    [Fact]
    public async Task TokenWithoutAJti_IsRejected()
    {
        // A pre-rotation token: signed correctly, type claim absent, no jti.
        using var c = Client();
        var legacy = new JwtTokenService(new AgentSettings()).IssueRefreshToken(Email, "customer");
        (await RefreshAsync(c, legacy)).StatusCode.Should().Be(HttpStatusCode.Unauthorized);
    }

    [Fact]
    public async Task UnknownJti_IsRejected()
    {
        using var c = Client();
        var forged = new JwtTokenService(new AgentSettings()).IssueRefreshToken(Email, "customer", Guid.NewGuid().ToString());
        (await RefreshAsync(c, forged)).StatusCode.Should().Be(HttpStatusCode.Unauthorized);
    }

    [Fact]
    public async Task AccessToken_CannotBeUsedAsARefreshToken()
    {
        using var c = Client();
        var login = await c.PostAsJsonAsync("/api/auth/login", new { email = Email, password = Password });
        var access = (await Json(login)).GetProperty("access_token").GetString()!;
        (await RefreshAsync(c, access)).StatusCode.Should().Be(HttpStatusCode.Unauthorized);
    }

    [Fact]
    public async Task ExpiredRow_IsRejected()
    {
        using var c = Client();
        var token = await LoginAsync(c);
        await using (var conn = await _pool.OpenAsync())
        {
            await conn.ExecuteAsync("UPDATE refresh_tokens SET expires_at = NOW() - INTERVAL '1 second'");
        }
        (await RefreshAsync(c, token)).StatusCode.Should().Be(HttpStatusCode.Unauthorized);
    }

    [Fact]
    public async Task FlagOff_KeepsTheOldContract()
    {
        using var c = Client(rotation: false);
        var token = await LoginAsync(c);
        var a = await RefreshAsync(c, token);
        var b = await RefreshAsync(c, token);
        a.StatusCode.Should().Be(HttpStatusCode.OK);
        b.StatusCode.Should().Be(HttpStatusCode.OK);
        (await Json(a)).TryGetProperty("refresh_token", out _).Should().BeFalse();
        await using var conn = await _pool.OpenAsync();
        (await conn.ExecuteScalarAsync<int>("SELECT COUNT(*) FROM refresh_tokens")).Should().Be(0);
    }

    // ───────────────────────── signup validation ─────────────────────────

    public static IEnumerable<object[]> BadSignups()
    {
        const string pw = "Password must be between 8 and 72 bytes long";
        const string nm = "Name must be between 1 and 100 characters";
        yield return new object[] { "not-an-email", "longenough1", "N", "Invalid email address" };
        yield return new object[] { "a@b", "longenough1", "N", "Invalid email address" };
        yield return new object[] { "two words@example.com", "longenough1", "N", "Invalid email address" };
        yield return new object[] { "", "longenough1", "N", "Invalid email address" };
        yield return new object[] { "n@example.com", "short7!", "N", pw };
        yield return new object[] { "n@example.com", new string('a', 73), "N", pw };
        yield return new object[] { "n@example.com", new string('é', 37), "N", pw }; // 37 chars, 74 bytes
        yield return new object[] { "n@example.com", "longenough1", "", nm };
        yield return new object[] { "n@example.com", "longenough1", "   ", nm };
        yield return new object[] { "n@example.com", "longenough1", new string('n', 101), nm };
    }

    [Theory]
    [MemberData(nameof(BadSignups))]
    public async Task Signup_RejectsBadInput_With400AndAMessage(string email, string password, string name, string message)
    {
        using var c = Client();
        var r = await c.PostAsJsonAsync("/api/auth/signup", new { email, password, name });
        r.StatusCode.Should().Be(HttpStatusCode.BadRequest);
        (await Json(r)).GetProperty("detail").GetString().Should().Be(message);
    }

    [Fact]
    public async Task Signup_AcceptsTheBoundaries_Normalises_AndIgnoresTheRoleField()
    {
        using var c = Client();
        var r = await c.PostAsJsonAsync("/api/auth/signup", new
        {
            email = "  Mixed.Case@Example.COM ",
            password = new string('a', 72),
            name = new string('n', 100),
            role = "admin",
        });
        r.StatusCode.Should().Be(HttpStatusCode.OK);
        var user = (await Json(r)).GetProperty("user");
        user.GetProperty("email").GetString().Should().Be("mixed.case@example.com");
        user.GetProperty("role").GetString().Should().Be("customer");
        user.GetProperty("name").GetString().Should().HaveLength(100);

        // `name` (what the web client sends) is honoured, login is case-insensitive, and a
        // re-signup under different casing is a conflict.
        var login = await c.PostAsJsonAsync("/api/auth/login", new { email = "MIXED.case@example.com", password = new string('a', 72) });
        login.StatusCode.Should().Be(HttpStatusCode.OK);
        (await c.PostAsJsonAsync("/api/auth/signup", new { email = "MIXED.CASE@example.com", password = "longenough1", name = "x" }))
            .StatusCode.Should().Be(HttpStatusCode.Conflict);
    }

    [Fact]
    public async Task OverlongPasswordOnLogin_Is401Not500()
    {
        using var c = Client();
        var r = await c.PostAsJsonAsync("/api/auth/login", new { email = Email, password = new string('x', 200) });
        r.StatusCode.Should().Be(HttpStatusCode.Unauthorized);
    }
}
