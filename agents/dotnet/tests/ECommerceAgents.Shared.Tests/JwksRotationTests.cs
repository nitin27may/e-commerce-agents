using ECommerceAgents.Shared.Auth;
using ECommerceAgents.Shared.Configuration;
using FluentAssertions;
using Microsoft.IdentityModel.Tokens;
using System.IdentityModel.Tokens.Jwt;
using System.Net;
using System.Security.Claims;
using System.Security.Cryptography;
using System.Text;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// Signing-key rotation on the validating side: an unknown <c>kid</c> refetches the JWKS once, rate-limited,
/// so a key the auth-server rotated in needs no restart. The HTTP layer is a stub handler over a mutable JWKS.
/// </summary>
public sealed class JwksRotationTests
{
    private const string Issuer = "http://auth-server:8090";
    private const string Audience = "ecommerce-orchestrator";

    private static readonly AgentSettings Settings = new()
    {
        AgentSharedSecret = new string('s', 48),
        JwtSecret = new string('j', 48),
        Environment = "test",
        AuthServerIssuer = Issuer,
        AuthJwksCacheTtl = 900,
    };

    private sealed class PublishedJwks : HttpMessageHandler
    {
        public List<RsaSecurityKey> Keys { get; } = [];
        public int Fetches { get; private set; }
        public bool Down { get; set; }

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct)
        {
            Fetches++;
            if (Down)
            {
                throw new HttpRequestException("auth-server unreachable");
            }

            var entries = Keys.Select(k =>
            {
                var p = k.Rsa!.ExportParameters(false);
                return $$"""{"kty":"RSA","use":"sig","kid":"{{k.KeyId}}","alg":"RS256","n":"{{Base64UrlEncoder.Encode(p.Modulus)}}","e":"{{Base64UrlEncoder.Encode(p.Exponent)}}"}""";
            });
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent($$"""{"keys":[{{string.Join(",", entries)}}]}""", Encoding.UTF8, "application/json"),
            });
        }
    }

    private sealed class ManualTime : TimeProvider
    {
        private DateTimeOffset _now = new(2026, 1, 1, 0, 0, 0, TimeSpan.Zero);

        public override DateTimeOffset GetUtcNow() => _now;

        public void Advance(TimeSpan by) => _now += by;
    }

    private static RsaSecurityKey NewKey() => new(RSA.Create(2048)) { KeyId = Guid.NewGuid().ToString("N") };

    private static string Token(RsaSecurityKey key) =>
        new JwtSecurityTokenHandler().WriteToken(
            new JwtSecurityToken(
                issuer: Issuer,
                audience: Audience,
                claims: [new Claim("sub", "alice@example.com"), new Claim("scope", "api:chat")],
                notBefore: DateTime.UtcNow.AddMinutes(-1),
                expires: DateTime.UtcNow.AddMinutes(5),
                signingCredentials: new SigningCredentials(key, SecurityAlgorithms.RsaSha256)
            )
        );

    private static (JwksKeyProvider provider, PublishedJwks jwks, ManualTime time, RsaSecurityKey oldKey) Arrange()
    {
        var oldKey = NewKey();
        var jwks = new PublishedJwks { Keys = { oldKey } };
        var time = new ManualTime();
        return (new JwksKeyProvider(new HttpClient(jwks), Settings, time), jwks, time, oldKey);
    }

    private static void ShouldValidate(IReadOnlyList<SecurityKey> keys, string token) =>
        new JwtTokenService(Settings)
            .ValidateOAuth(token, keys, Audience, "api:chat")
            .FindFirst("sub")!.Value.Should().Be("alice@example.com");

    [Fact]
    public async Task KnownKid_DoesNotRefetch()
    {
        var (provider, jwks, _, oldKey) = Arrange();

        for (var i = 0; i < 3; i++)
        {
            ShouldValidate(await provider.GetSigningKeysForTokenAsync(Token(oldKey)), Token(oldKey));
        }

        jwks.Fetches.Should().Be(1);
    }

    [Fact]
    public async Task RotatedInKey_IsPickedUpWithoutRestart_AndTheOldKeyStillValidates()
    {
        var (provider, jwks, _, oldKey) = Arrange();
        await provider.GetSigningKeysForTokenAsync(Token(oldKey)); // warm the cache with the pre-rotation JWKS
        var newKey = NewKey();
        jwks.Keys.Add(newKey); // the auth-server rotated; the old key stays published

        var newToken = Token(newKey);
        ShouldValidate(await provider.GetSigningKeysForTokenAsync(newToken), newToken);
        ShouldValidate(await provider.GetSigningKeysForTokenAsync(Token(oldKey)), Token(oldKey));

        jwks.Fetches.Should().Be(2); // warm-up + one refresh for the unknown kid
    }

    [Fact]
    public async Task UnknownKidRefresh_IsRateLimited()
    {
        var (provider, jwks, time, oldKey) = Arrange();
        await provider.GetSigningKeysForTokenAsync(Token(oldKey));
        var stranger = Token(NewKey()); // never published

        for (var i = 0; i < 5; i++)
        {
            var keys = await provider.GetSigningKeysForTokenAsync(stranger);
            var act = () => new JwtTokenService(Settings).ValidateOAuth(stranger, keys, Audience, "api:chat");
            act.Should().Throw<SecurityTokenException>();
        }

        jwks.Fetches.Should().Be(2); // warm-up + exactly one refresh, not one per attempt

        time.Advance(JwksKeyProvider.UnknownKidRefreshCooldown + TimeSpan.FromSeconds(1));
        await provider.GetSigningKeysForTokenAsync(stranger);
        jwks.Fetches.Should().Be(3); // the window reopened
    }

    [Fact]
    public async Task KeyPublishedInsideTheCooldown_IsFoundAfterIt()
    {
        var (provider, jwks, time, oldKey) = Arrange();
        await provider.GetSigningKeysForTokenAsync(Token(oldKey));
        var newKey = NewKey();
        var newToken = Token(newKey);

        await provider.GetSigningKeysForTokenAsync(newToken); // spends the refresh before the key exists
        jwks.Keys.Add(newKey);
        var cooling = await provider.GetSigningKeysForTokenAsync(newToken);
        cooling.Any(k => k.KeyId == newKey.KeyId).Should().BeFalse(); // fails closed inside the window

        time.Advance(JwksKeyProvider.UnknownKidRefreshCooldown + TimeSpan.FromSeconds(1));
        ShouldValidate(await provider.GetSigningKeysForTokenAsync(newToken), newToken);
    }

    [Fact]
    public async Task FailedRefetch_ServesCachedKeysAndIsStillRateLimited()
    {
        var (provider, jwks, _, oldKey) = Arrange();
        await provider.GetSigningKeysForTokenAsync(Token(oldKey));
        jwks.Down = true;
        var stranger = Token(NewKey());

        for (var i = 0; i < 3; i++)
        {
            var keys = await provider.GetSigningKeysForTokenAsync(stranger);
            keys.Should().ContainSingle(k => k.KeyId == oldKey.KeyId);
        }

        jwks.Fetches.Should().Be(2); // warm-up + one failed attempt; the outage is not hammered
    }

    [Fact]
    public async Task UnreadableToken_DoesNotRefetch()
    {
        var (provider, jwks, _, oldKey) = Arrange();
        await provider.GetSigningKeysForTokenAsync(Token(oldKey));

        await provider.GetSigningKeysForTokenAsync("not-a-jwt");
        await provider.GetSigningKeysForTokenAsync("");

        jwks.Fetches.Should().Be(1);
    }
}
