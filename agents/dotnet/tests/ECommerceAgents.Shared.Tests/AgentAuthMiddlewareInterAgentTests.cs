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
using System.IdentityModel.Tokens.Jwt;
using System.Net;
using System.Net.Http.Json;
using System.Security.Claims;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// Phase C: <see cref="AgentAuthMiddleware"/>'s inter-agent oauth branch —
/// a real RS256 service token (self-signed per test, JWKS fetch stubbed to
/// serve its public key — no real network call) authenticating a request
/// with forwarded X-User-* headers. Zero prior test coverage existed for
/// this middleware in .NET before this phase.
/// </summary>
public sealed class AgentAuthMiddlewareInterAgentTests
{
    private static (RsaSecurityKey key, string kid) NewRsaKey()
    {
        var rsa = RSA.Create(2048);
        var kid = Guid.NewGuid().ToString("N");
        return (new RsaSecurityKey(rsa) { KeyId = kid }, kid);
    }

    private static string IssueToken(
        RsaSecurityKey key,
        string issuer,
        string audience,
        string scope,
        DateTime? expires = null
    )
    {
        var handler = new JwtSecurityTokenHandler();
        var creds = new SigningCredentials(key, SecurityAlgorithms.RsaSha256);
        var token = new JwtSecurityToken(
            issuer: issuer,
            audience: audience,
            claims: new[] { new Claim("scope", scope) },
            notBefore: DateTime.UtcNow.AddMinutes(-30),
            expires: expires ?? DateTime.UtcNow.AddMinutes(5),
            signingCredentials: creds
        );
        return handler.WriteToken(token);
    }

    private static string BuildJwksJson(RsaSecurityKey key)
    {
        var parameters = key.Rsa!.ExportParameters(false);
        var n = Base64UrlEncoder.Encode(parameters.Modulus);
        var e = Base64UrlEncoder.Encode(parameters.Exponent);
        return $$"""{"keys":[{"kty":"RSA","use":"sig","kid":"{{key.KeyId}}","alg":"RS256","n":"{{n}}","e":"{{e}}"}]}""";
    }

    private sealed class StaticResponseHandler(string body) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct) =>
            Task.FromResult(
                new HttpResponseMessage(HttpStatusCode.OK)
                {
                    Content = new StringContent(body, Encoding.UTF8, "application/json"),
                }
            );
    }

    private static AgentSettings SettingsFor() =>
        new()
        {
            AgentSharedSecret = new string('s', 48),
            JwtSecret = new string('j', 48),
            Environment = "test",
            AuthMode = "oauth",
            AuthServerIssuer = "http://auth-server:8090",
            AuthAgentAudience = "ecommerce-agents",
            AuthOrchAudience = "ecommerce-orchestrator",
        };

    private static TestServer BuildServer(AgentSettings settings, RsaSecurityKey key, bool isOrchestrator = false)
    {
        var hostBuilder = new HostBuilder().ConfigureWebHost(web =>
        {
            web.UseTestServer();
            web.ConfigureServices(services =>
            {
                services.AddSingleton(settings);
                services.AddSingleton(new JwtTokenService(settings));
                services.AddSingleton(
                    new JwksKeyProvider(new HttpClient(new StaticResponseHandler(BuildJwksJson(key))), settings)
                );
                services.AddRouting();
            });
            web.Configure(app =>
            {
                app.UseAgentAuth(isOrchestrator);
                app.UseRouting();
                app.UseEndpoints(endpoints =>
                {
                    endpoints.MapPost(
                        "/x",
                        async ctx =>
                        {
                            ctx.Response.ContentType = "application/json";
                            await ctx.Response.WriteAsync(
                                JsonSerializer.Serialize(
                                    new
                                    {
                                        email = RequestContext.CurrentUserEmail,
                                        role = RequestContext.CurrentUserRole,
                                        sessionId = RequestContext.CurrentSessionId,
                                    }
                                )
                            );
                        }
                    );
                });
            });
        });

        return hostBuilder.Start().GetTestServer();
    }

    private static HttpRequestMessage Request(string? token, IDictionary<string, string>? extraHeaders = null)
    {
        var request = new HttpRequestMessage(HttpMethod.Post, "/x");
        if (token is not null)
        {
            request.Headers.Add("Authorization", $"Bearer {token}");
        }
        if (extraHeaders is not null)
        {
            foreach (var (key, value) in extraHeaders)
            {
                request.Headers.Add(key, value);
            }
        }
        return request;
    }

    [Fact]
    public async Task RealServiceToken_AuthenticatesInterAgentCallAndForwardsIdentity()
    {
        var (key, _) = NewRsaKey();
        var settings = SettingsFor();
        var token = IssueToken(key, settings.AuthServerIssuer, "ecommerce-agents", "agent:invoke");
        using var server = BuildServer(settings, key);
        using var client = server.CreateClient();

        var response = await client.SendAsync(
            Request(
                token,
                new Dictionary<string, string>
                {
                    ["X-User-Email"] = "alice@example.com",
                    ["X-User-Role"] = "admin",
                    ["X-Session-Id"] = "sess-1",
                }
            )
        );

        response.StatusCode.Should().Be(HttpStatusCode.OK);
        var body = await response.Content.ReadFromJsonAsync<JsonElement>();
        body.GetProperty("email").GetString().Should().Be("alice@example.com");
        body.GetProperty("role").GetString().Should().Be("admin");
        body.GetProperty("sessionId").GetString().Should().Be("sess-1");
    }

    [Fact]
    public async Task RealServiceToken_NoForwardedHeaders_DefaultsToSystem()
    {
        var (key, _) = NewRsaKey();
        var settings = SettingsFor();
        var token = IssueToken(key, settings.AuthServerIssuer, "ecommerce-agents", "agent:invoke");
        using var server = BuildServer(settings, key);
        using var client = server.CreateClient();

        var response = await client.SendAsync(Request(token));

        response.StatusCode.Should().Be(HttpStatusCode.OK);
        var body = await response.Content.ReadFromJsonAsync<JsonElement>();
        body.GetProperty("email").GetString().Should().Be("system");
        body.GetProperty("role").GetString().Should().Be("system");
    }

    [Fact]
    public async Task WrongAudienceToken_Rejected()
    {
        var (key, _) = NewRsaKey();
        var settings = SettingsFor();
        // A token issued for the orchestrator's own api:chat scope must not
        // authenticate an inter-agent call expecting agent:invoke.
        var token = IssueToken(key, settings.AuthServerIssuer, "ecommerce-orchestrator", "api:chat");
        using var server = BuildServer(settings, key);
        using var client = server.CreateClient();

        var response = await client.SendAsync(
            Request(token, new Dictionary<string, string> { ["X-User-Email"] = "alice@example.com" })
        );

        response.StatusCode.Should().Be(HttpStatusCode.Unauthorized);
    }

    [Fact]
    public async Task WrongScopeToken_Rejected()
    {
        var (key, _) = NewRsaKey();
        var settings = SettingsFor();
        var token = IssueToken(key, settings.AuthServerIssuer, "ecommerce-agents", "mcp:product");
        using var server = BuildServer(settings, key);
        using var client = server.CreateClient();

        var response = await client.SendAsync(Request(token));

        response.StatusCode.Should().Be(HttpStatusCode.Unauthorized);
    }

    [Fact]
    public async Task ExpiredServiceToken_Rejected()
    {
        var (key, _) = NewRsaKey();
        var settings = SettingsFor();
        var token = IssueToken(
            key,
            settings.AuthServerIssuer,
            "ecommerce-agents",
            "agent:invoke",
            expires: DateTime.UtcNow.AddMinutes(-10)
        );
        using var server = BuildServer(settings, key);
        using var client = server.CreateClient();

        var response = await client.SendAsync(Request(token));

        response.StatusCode.Should().Be(HttpStatusCode.Unauthorized);
    }

    [Fact]
    public async Task AgentSecret_RejectedOutrightWhenOauthModeActive()
    {
        var (key, _) = NewRsaKey();
        var settings = SettingsFor();
        using var server = BuildServer(settings, key);
        using var client = server.CreateClient();

        var request = new HttpRequestMessage(HttpMethod.Post, "/x");
        request.Headers.Add("X-Agent-Secret", settings.AgentSharedSecret);
        var response = await client.SendAsync(request);

        response.StatusCode.Should().Be(HttpStatusCode.Unauthorized);
    }

    [Fact]
    public async Task SpoofedRole_RejectedUnderStrictIdentity()
    {
        var (key, _) = NewRsaKey();
        var settings = SettingsFor() with { GuardrailsStrictIdentity = true };
        var token = IssueToken(key, settings.AuthServerIssuer, "ecommerce-agents", "agent:invoke");
        using var server = BuildServer(settings, key);
        using var client = server.CreateClient();

        var response = await client.SendAsync(
            Request(
                token,
                new Dictionary<string, string>
                {
                    ["X-User-Email"] = "alice@example.com",
                    ["X-User-Role"] = "superadmin",
                }
            )
        );

        response.StatusCode.Should().Be(HttpStatusCode.Unauthorized);
    }

    [Theory]
    [InlineData("support")]
    [InlineData("power_user")]
    public async Task StaffAndPowerUserRoles_AcceptedUnderStrictIdentity(string role)
    {
        var (key, _) = NewRsaKey();
        var settings = SettingsFor() with { GuardrailsStrictIdentity = true };
        var token = IssueToken(key, settings.AuthServerIssuer, "ecommerce-agents", "agent:invoke");
        using var server = BuildServer(settings, key);
        using var client = server.CreateClient();

        var response = await client.SendAsync(
            Request(
                token,
                new Dictionary<string, string>
                {
                    ["X-User-Email"] = "alice@example.com",
                    ["X-User-Role"] = role,
                }
            )
        );

        response.StatusCode.Should().Be(HttpStatusCode.OK);
    }

    [Fact]
    public async Task SpoofedRole_AllowedWhenNotStrict()
    {
        var (key, _) = NewRsaKey();
        var settings = SettingsFor() with { GuardrailsStrictIdentity = false };
        var token = IssueToken(key, settings.AuthServerIssuer, "ecommerce-agents", "agent:invoke");
        using var server = BuildServer(settings, key);
        using var client = server.CreateClient();

        var response = await client.SendAsync(
            Request(
                token,
                new Dictionary<string, string>
                {
                    ["X-User-Email"] = "alice@example.com",
                    ["X-User-Role"] = "superadmin",
                }
            )
        );

        response.StatusCode.Should().Be(HttpStatusCode.OK); // observe-only: logged, not blocked
    }

    [Fact]
    public async Task OrchestratorMode_StillValidatesUserAudience()
    {
        // isOrchestrator=true must keep validating the ORCH audience/scope
        // even though AuthMode=oauth — the inter-agent branch is only taken
        // when isOrchestrator=false.
        var (key, _) = NewRsaKey();
        var settings = SettingsFor();
        var userToken = IssueToken(key, settings.AuthServerIssuer, "ecommerce-orchestrator", "api:chat");
        using var server = BuildServer(settings, key, isOrchestrator: true);
        using var client = server.CreateClient();

        var response = await client.SendAsync(Request(userToken));

        response.StatusCode.Should().Be(HttpStatusCode.OK);
    }

    [Fact]
    public async Task OrchestratorMode_RejectsAgentAudienceToken()
    {
        var (key, _) = NewRsaKey();
        var settings = SettingsFor();
        var serviceToken = IssueToken(key, settings.AuthServerIssuer, "ecommerce-agents", "agent:invoke");
        using var server = BuildServer(settings, key, isOrchestrator: true);
        using var client = server.CreateClient();

        var response = await client.SendAsync(Request(serviceToken));

        response.StatusCode.Should().Be(HttpStatusCode.Unauthorized);
    }
}
