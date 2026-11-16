using System.IdentityModel.Tokens.Jwt;
using System.Net;
using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using ECommerceAgents.Shared.Auth;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using FluentAssertions;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.TestHost;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.IdentityModel.Tokens;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// B-14, the .NET half: the signed user-context token (<see cref="UserContextToken"/>) and where a specialist takes
/// identity from. Mirrors the Python <c>tests/test_identity_token.py</c>, and verifies the same golden vectors
/// (<c>agents/python/config/identity/testdata/user-context.v1.json</c>) that Python mints, so the two stacks cannot
/// drift apart on the key derivation or the token format.
/// </summary>
public sealed class UserContextTokenTests
{
    private static readonly byte[] Key = UserContextToken.DeriveKey("test-identity-secret-0123456789abcdef");
    private static readonly byte[] OtherKey = UserContextToken.DeriveKey("another-identity-secret-0123456789abc");
    private const string Audience = "product-discovery";
    private const string Alice = "alice@example.com";
    private const string Session = "6f1c0a5e-2d3b-4c7e-9a10-1b2c3d4e5f60";

    private static JsonElement Vectors()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !Directory.Exists(Path.Combine(dir.FullName, "agents", "python", "config")))
        {
            dir = dir.Parent;
        }

        dir.Should().NotBeNull();
        var path = Path.Combine(dir!.FullName, "agents", "python", "config", "identity", "testdata", "user-context.v1.json");
        return JsonDocument.Parse(File.ReadAllText(path)).RootElement.Clone();
    }

    private static string Mint(string audience = Audience, byte[]? key = null, int ttl = 300, long? now = null, string email = Alice,
        string role = "customer", string session = Session) =>
        UserContextToken.Mint(email, role, session, audience, key ?? Key, ttl, now);

    // ─────────────────────── derivation and the cross-stack vectors ───────────────────────

    [Fact]
    public void TheKeyDerivation_MatchesAReferenceHkdf()
    {
        foreach (var secret in new[] { "a", new string('x', 40), "päss wörd ☃" })
        {
            // Independent RFC 5869 implementation (extract with a zero salt, expand one block).
            var prk = HMACSHA256.HashData(new byte[32], Encoding.UTF8.GetBytes(secret));
            var expand = HMACSHA256.HashData(prk, (byte[])[.. Encoding.UTF8.GetBytes(UserContextToken.KdfInfo), 1]);

            UserContextToken.DeriveKey(secret).Should().Equal(expand);
        }
    }

    [Fact]
    public void TheGoldenVectors_PythonMints_AreVerifiedHere()
    {
        var vectors = Vectors();
        var secret = vectors.GetProperty("secret").GetString()!;
        var key = UserContextToken.DeriveKey(secret);

        Convert.ToHexString(key).ToLowerInvariant().Should().Be(vectors.GetProperty("derived_key_hex").GetString());
        vectors.GetProperty("kdf_info").GetString().Should().Be(UserContextToken.KdfInfo);

        var audience = vectors.GetProperty("audience").GetString()!;
        var verifyAt = vectors.GetProperty("verify_at").GetInt64();
        var cases = 0;
        foreach (var entry in vectors.GetProperty("tokens").EnumerateObject())
        {
            var verified = UserContextToken.Verify(entry.Value.GetProperty("token").GetString()!, audience, key, verifyAt);

            verified.Email.Should().Be(entry.Value.GetProperty("email").GetString(), entry.Name);
            verified.Role.Should().Be(entry.Value.GetProperty("role").GetString(), entry.Name);
            verified.SessionId.Should().Be(entry.Value.GetProperty("session_id").GetString(), entry.Name);
            cases++;
        }

        cases.Should().BeGreaterThan(1);
    }

    [Fact]
    public void TheGoldenVectors_AreRefusedAfterTheyExpire()
    {
        var vectors = Vectors();
        var key = UserContextToken.DeriveKey(vectors.GetProperty("secret").GetString()!);
        var token = vectors.GetProperty("tokens").GetProperty("customer").GetProperty("token").GetString()!;
        var expired = vectors.GetProperty("issued_at").GetInt64() + vectors.GetProperty("ttl_seconds").GetInt32() + 3600;

        var verify = () => UserContextToken.Verify(token, Audience, key, expired);

        verify.Should().Throw<IdentityTokenException>().Which.Reason.Should().Be("expired");
    }

    public static TheoryData<string> NegativeVectorNames()
    {
        var names = new TheoryData<string>();
        foreach (var entry in Vectors().GetProperty("negative").EnumerateArray())
        {
            names.Add(entry.GetProperty("name").GetString()!);
        }

        return names;
    }

    /// <summary>
    /// Every entry of the shared file's <c>negative</c> list must be refused here exactly as Python refuses it
    /// (<c>tests/test_identity_token.py</c> iterates the same list), so neither stack can accept what the other
    /// refuses. The clock is the file's pinned <c>verify_at</c> (or the entry's own), never the wall clock.
    /// </summary>
    [Theory]
    [MemberData(nameof(NegativeVectorNames))]
    public void EveryNegativeVector_IsRefused(string name)
    {
        var vectors = Vectors();
        var entry = vectors.GetProperty("negative").EnumerateArray().Single(e => e.GetProperty("name").GetString() == name);
        entry.GetProperty("expected").GetString().Should().Be("refused");
        var key = UserContextToken.DeriveKey(vectors.GetProperty("secret").GetString()!);
        var at = entry.TryGetProperty("verify_at", out var own) ? own.GetInt64() : vectors.GetProperty("verify_at").GetInt64();

        var verify = () => UserContextToken.Verify(entry.GetProperty("token").GetString()!, vectors.GetProperty("audience").GetString()!, key, at);

        verify.Should().Throw<IdentityTokenException>(name);
    }

    [Fact]
    public void TheNegativeVectors_CoverEveryRule()
    {
        var names = Vectors().GetProperty("negative").EnumerateArray().Select(e => e.GetProperty("name").GetString()).ToHashSet();

        names.Should().Contain([
            "expired", "iat_in_the_future", "nbf_in_the_future", "missing_sub", "iat_is_a_float", "exp_is_a_float",
            "wrong_typ", "wrong_audience", "alg_none", "lifetime_over_15_minutes", "bad_signature",
        ]);
    }

    // ─────────────────────── the token ───────────────────────

    [Fact]
    public void ATokenRoundTrips_AndNamesItsAudience()
    {
        var verified = UserContextToken.Verify(Mint(), Audience, Key);

        (verified.Email, verified.Role, verified.SessionId, verified.Audience).Should().Be((Alice, "customer", Session, Audience));
    }

    [Fact]
    public void TheClaims_AreTheDocumentedOnes()
    {
        var jwt = new JwtSecurityTokenHandler().ReadJwtToken(Mint());

        jwt.Header.Alg.Should().Be("HS256");
        jwt.Header.Typ.Should().Be(UserContextToken.TokenType);
        jwt.Issuer.Should().Be("ecommerce-agents/orchestrator");
        jwt.Audiences.Should().Equal(Audience);
        jwt.Subject.Should().Be(Alice);
        jwt.Payload["act"].ToString().Should().Contain("\"client_id\":\"orchestrator\"");
        (jwt.Payload.Expiration - (long)Convert.ToInt64(jwt.Payload["iat"])).Should().Be(300);
        jwt.Payload["jti"].Should().NotBeNull();
    }

    [Fact]
    public void AnAnonymousCaller_KeepsItsExactIdentity()
    {
        var token = Mint(email: "", role: "anonymous", session: "");

        new JwtSecurityTokenHandler().ReadJwtToken(token).Subject.Should().Be("anonymous");
        var verified = UserContextToken.Verify(token, Audience, Key);
        (verified.Email, verified.Role, verified.SessionId).Should().Be(("", "anonymous", ""));
    }

    [Fact]
    public void EveryToken_IsUnique() => Mint().Should().NotBe(Mint());

    [Theory]
    [InlineData("expired")]
    [InlineData("signature")]
    [InlineData("audience")]
    [InlineData("lifetime")]
    [InlineData("malformed")]
    public void ATokenThatMustNotBeBelieved_IsRefused(string reason)
    {
        var now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
        var token = reason switch
        {
            "expired" => Mint(now: now - 3600),
            "signature" => Mint(key: OtherKey),
            "audience" => Mint(audience: "order-management"),
            "lifetime" => Mint(ttl: UserContextToken.MaxLifetimeSeconds + 600),
            _ => "not-a-jwt",
        };

        var verify = () => UserContextToken.Verify(token, Audience, Key);

        verify.Should().Throw<IdentityTokenException>().Which.Reason.Should().Be(reason);
    }

    [Fact]
    public void ALoginJwt_IsNotAnIdentityToken()
    {
        var login = new JwtSecurityTokenHandler().WriteToken(new JwtSecurityToken(
            claims: [new System.Security.Claims.Claim("sub", Alice)],
            expires: DateTime.UtcNow.AddMinutes(1),
            signingCredentials: new SigningCredentials(new SymmetricSecurityKey(Key), SecurityAlgorithms.HmacSha256)));

        var verify = () => UserContextToken.Verify(login, Audience, Key);

        verify.Should().Throw<IdentityTokenException>().Which.Reason.Should().Be("wrong_type");
    }

    [Theory]
    [InlineData(SecurityAlgorithms.HmacSha512)]
    [InlineData(SecurityAlgorithms.HmacSha384)]
    public void OnlyHs256IsAccepted(string algorithm)
    {
        var key = new SymmetricSecurityKey(new byte[64]);
        var header = new JwtHeader(new SigningCredentials(key, algorithm)) { ["typ"] = UserContextToken.TokenType };
        var now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
        var payload = new JwtPayload
        {
            ["iss"] = UserContextToken.Issuer, ["aud"] = Audience, ["sub"] = Alice, ["email"] = Alice, ["role"] = "admin",
            ["sid"] = "", ["iat"] = now, ["exp"] = now + 60,
        };
        var forged = new JwtSecurityTokenHandler().WriteToken(new JwtSecurityToken(header, payload));

        var verify = () => UserContextToken.Verify(forged, Audience, Key);

        verify.Should().Throw<IdentityTokenException>().Which.Reason.Should().Be("wrong_type");
    }

    [Fact]
    public void AlgNone_IsRefused()
    {
        var now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
        string B64(string json) => Base64UrlEncoder.Encode(json);
        var unsigned = B64("{\"alg\":\"none\",\"typ\":\"" + UserContextToken.TokenType + "\"}") + "."
            + B64($"{{\"iss\":\"{UserContextToken.Issuer}\",\"aud\":\"{Audience}\",\"sub\":\"{Alice}\",\"iat\":{now},\"exp\":{now + 60}}}") + ".";

        var verify = () => UserContextToken.Verify(unsigned, Audience, Key);

        verify.Should().Throw<IdentityTokenException>();
    }

    [Fact]
    public void ATamperedPayload_FailsItsSignature()
    {
        var parts = Mint(role: "customer").Split('.');
        var payload = Encoding.UTF8.GetString(Base64UrlEncoder.DecodeBytes(parts[1])).Replace("\"customer\"", "\"admin\"");
        var forged = $"{parts[0]}.{Base64UrlEncoder.Encode(payload)}.{parts[2]}";

        var verify = () => UserContextToken.Verify(forged, Audience, Key);

        verify.Should().Throw<IdentityTokenException>().Which.Reason.Should().Be("signature");
    }

    [Fact]
    public void TheKey_IsAConfigChoice_DistinctFromTheSharedSecret()
    {
        var withSecret = new AgentSettings { AgentSharedSecret = "inter-agent-secret-0123456789abcdef" };
        var withKey = new AgentSettings { AgentSharedSecret = withSecret.AgentSharedSecret, A2aIdentityKey = "a-different-identity-key-0123456789ab" };

        UserContextToken.ConfiguredKey(withSecret).Should().Equal(UserContextToken.DeriveKey(withSecret.AgentSharedSecret));
        UserContextToken.ConfiguredKey(withKey).Should().Equal(UserContextToken.DeriveKey("a-different-identity-key-0123456789ab"));
        UserContextToken.ConfiguredKey(withKey).Should().NotEqual(UserContextToken.ConfiguredKey(withSecret));
    }

    // ─────────────────────── the receiving side: AgentAuthMiddleware ───────────────────────

    private const string Secret = "inter-agent-secret-0123456789abcdef";

    private static TestServer Specialist(string mode, bool strict = false)
    {
        var settings = new AgentSettings
        {
            AgentSharedSecret = Secret,
            JwtSecret = new string('j', 48),
            Environment = "test",
            AuthMode = "local",
            A2aIdentity = mode,
            GuardrailsStrictIdentity = strict,
        };
        return new HostBuilder().ConfigureWebHost(web =>
        {
            web.UseTestServer();
            web.ConfigureServices(services =>
            {
                services.AddSingleton(settings);
                services.AddSingleton(new JwtTokenService(settings));
                services.AddSingleton(new JwksKeyProvider(new HttpClient(), settings));
                services.AddRouting();
            });
            web.Configure(app =>
            {
                app.UseAgentAuth(isOrchestrator: false, agentName: Audience);
                app.UseRouting();
                app.UseEndpoints(endpoints => endpoints.MapGet("/who", ctx => ctx.Response.WriteAsync(
                    JsonSerializer.Serialize(new
                    {
                        email = RequestContext.CurrentUserEmail,
                        role = RequestContext.CurrentUserRole,
                        session = RequestContext.CurrentSessionId,
                    }))));
            });
        }).Start().GetTestServer();
    }

    private static string SignedFor(string audience = Audience, long? now = null, string email = Alice, string role = "customer",
        string session = Session, byte[]? key = null) =>
        UserContextToken.Mint(email, role, session, audience, key ?? UserContextToken.DeriveKey(Secret), 300, now);

    private static async Task<HttpResponseMessage> Call(string mode, IDictionary<string, string> headers, bool credential = true, bool strict = false)
    {
        using var server = Specialist(mode, strict);
        using var client = server.CreateClient();
        var request = new HttpRequestMessage(HttpMethod.Get, "/who");
        if (credential)
        {
            request.Headers.Add("X-Agent-Secret", Secret);
        }

        foreach (var (name, value) in headers)
        {
            request.Headers.Add(name, value);
        }

        return await client.SendAsync(request);
    }

    private static readonly Dictionary<string, string> AliceHeaders = new()
    {
        ["X-User-Email"] = Alice, ["X-User-Role"] = "customer", ["X-Session-Id"] = Session,
    };

    [Theory]
    [InlineData("both")]
    [InlineData("token")]
    public async Task AValidToken_SetsTheIdentity(string mode)
    {
        var response = await Call(mode, new Dictionary<string, string> { [UserContextToken.HeaderName] = SignedFor() });

        response.StatusCode.Should().Be(HttpStatusCode.OK);
        var body = await response.Content.ReadFromJsonAsync<JsonElement>();
        (body.GetProperty("email").GetString(), body.GetProperty("role").GetString(), body.GetProperty("session").GetString())
            .Should().Be((Alice, "customer", Session));
    }

    [Theory]
    [InlineData("both")]
    [InlineData("token")]
    public async Task AForgedEmailBesideAValidTokenForAnotherUser_IsRefused(string mode)
    {
        var response = await Call(mode, new Dictionary<string, string>
        {
            [UserContextToken.HeaderName] = SignedFor(), ["X-User-Email"] = "admin@example.com",
        });

        response.StatusCode.Should().Be(HttpStatusCode.Unauthorized);
        (await response.Content.ReadAsStringAsync()).Should().Contain("Identity does not match the user context");
    }

    [Theory]
    [InlineData("both")]
    [InlineData("token")]
    public async Task AForgedRoleBesideAValidToken_IsRefused(string mode)
    {
        var response = await Call(mode, new Dictionary<string, string> { [UserContextToken.HeaderName] = SignedFor(), ["X-User-Role"] = "admin" });

        response.StatusCode.Should().Be(HttpStatusCode.Unauthorized);
    }

    [Fact]
    public async Task ASessionHeaderThatContradictsTheToken_IsRefused()
    {
        var response = await Call("both", new Dictionary<string, string>
        {
            [UserContextToken.HeaderName] = SignedFor(), ["X-Session-Id"] = "someone-elses-conversation",
        });

        response.StatusCode.Should().Be(HttpStatusCode.Unauthorized);
    }

    [Theory]
    [InlineData("both")]
    [InlineData("token")]
    public async Task AnExpiredToken_IsRefused(string mode)
    {
        var headers = new Dictionary<string, string>(AliceHeaders)
        {
            [UserContextToken.HeaderName] = SignedFor(now: DateTimeOffset.UtcNow.ToUnixTimeSeconds() - 3600),
        };

        var response = await Call(mode, headers);

        response.StatusCode.Should().Be(HttpStatusCode.Unauthorized);
        (await response.Content.ReadAsStringAsync()).Should().Contain("Invalid user context");
    }

    [Fact]
    public async Task AnInvalidToken_IsNeverIgnoredInFavourOfTheHeaders()
    {
        var headers = new Dictionary<string, string>(AliceHeaders) { [UserContextToken.HeaderName] = "garbage" };

        (await Call("both", headers)).StatusCode.Should().Be(HttpStatusCode.Unauthorized);
    }

    [Theory]
    [InlineData("both")]
    [InlineData("token")]
    public async Task ATokenForAnotherSpecialist_IsRefused(string mode) =>
        (await Call(mode, new Dictionary<string, string> { [UserContextToken.HeaderName] = SignedFor(audience: "order-management") }))
            .StatusCode.Should().Be(HttpStatusCode.Unauthorized);

    [Fact]
    public async Task ATokenSignedWithAnotherKey_IsRefused() =>
        (await Call("both", new Dictionary<string, string> { [UserContextToken.HeaderName] = SignedFor(key: OtherKey) }))
            .StatusCode.Should().Be(HttpStatusCode.Unauthorized);

    [Fact]
    public async Task BothMode_StillServesACallerThatSendsOnlyTheHeaders()
    {
        var response = await Call("both", AliceHeaders);

        response.StatusCode.Should().Be(HttpStatusCode.OK);
        (await response.Content.ReadFromJsonAsync<JsonElement>()).GetProperty("email").GetString().Should().Be(Alice);
    }

    [Fact]
    public async Task TokenMode_RefusesHeadersAlone()
    {
        var response = await Call("token", AliceHeaders);

        response.StatusCode.Should().Be(HttpStatusCode.Unauthorized);
        (await response.Content.ReadAsStringAsync()).Should().Contain("User context required");
    }

    [Fact]
    public async Task HeadersMode_IgnoresATokenEntirely()
    {
        var response = await Call("headers", new Dictionary<string, string>(AliceHeaders) { [UserContextToken.HeaderName] = "not even a jwt" });

        response.StatusCode.Should().Be(HttpStatusCode.OK);
        (await response.Content.ReadFromJsonAsync<JsonElement>()).GetProperty("email").GetString().Should().Be(Alice);

        // ...and does not believe a valid one over the headers either: the headers are the identity here.
        var other = await Call("headers", new Dictionary<string, string>
        {
            [UserContextToken.HeaderName] = SignedFor(), ["X-User-Email"] = "bob@example.com", ["X-User-Role"] = "customer",
        });
        (await other.Content.ReadFromJsonAsync<JsonElement>()).GetProperty("email").GetString().Should().Be("bob@example.com");
    }

    [Fact]
    public async Task TheTokenAlone_IsEnough_TheHeadersAreOptional()
    {
        var response = await Call("token", new Dictionary<string, string>
        {
            [UserContextToken.HeaderName] = SignedFor(email: "bob@example.com", role: "seller", session: ""),
        });

        var body = await response.Content.ReadFromJsonAsync<JsonElement>();
        (body.GetProperty("email").GetString(), body.GetProperty("role").GetString(), body.GetProperty("session").GetString())
            .Should().Be(("bob@example.com", "seller", ""));
    }

    [Fact]
    public async Task TheInterAgentCredential_IsStillRequired() =>
        (await Call("both", new Dictionary<string, string> { [UserContextToken.HeaderName] = SignedFor() }, credential: false))
            .StatusCode.Should().Be(HttpStatusCode.Unauthorized);

    [Fact]
    public async Task StrictIdentity_StillJudgesTheIdentityATokenCarries()
    {
        var response = await Call("token", new Dictionary<string, string> { [UserContextToken.HeaderName] = SignedFor(role: "not-a-role") }, strict: true);

        response.StatusCode.Should().Be(HttpStatusCode.Unauthorized);
        (await response.Content.ReadAsStringAsync()).Should().Contain("Invalid forwarded identity");
    }
}
