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
using Microsoft.Extensions.Logging.Abstractions;
using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// Local-mode <c>X-Agent-Secret</c> handling. The orchestrator is the
/// user-facing front door and must never honour it (otherwise a caller with
/// the shared secret could pick any identity via X-User-*); specialists must
/// still accept it, because that is how the orchestrator calls them.
/// </summary>
public sealed class AgentAuthMiddlewareSharedSecretTests
{
    private static readonly string Secret = new('s', 48);

    private static AgentSettings SettingsFor() =>
        new()
        {
            AgentSharedSecret = Secret,
            JwtSecret = new string('j', 48),
            Environment = "test",
            AuthMode = "local",
        };

    private static TestServer BuildServer(AgentSettings settings, bool isOrchestrator)
    {
        var hostBuilder = new HostBuilder().ConfigureWebHost(web =>
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
                app.UseAgentAuth(isOrchestrator);
                app.UseRouting();
                app.UseEndpoints(endpoints =>
                    endpoints.MapPost("/api/chat", ctx => ctx.Response.WriteAsync(
                        JsonSerializer.Serialize(new
                        {
                            email = RequestContext.CurrentUserEmail,
                            role = RequestContext.CurrentUserRole,
                        }))));
            });
        });
        return hostBuilder.Start().GetTestServer();
    }

    private static HttpRequestMessage Forged(string secret)
    {
        var request = new HttpRequestMessage(HttpMethod.Post, "/api/chat");
        request.Headers.Add("X-Agent-Secret", secret);
        request.Headers.Add("X-User-Email", "admin@example.com");
        request.Headers.Add("X-User-Role", "admin");
        return request;
    }

    [Fact]
    public async Task Orchestrator_RejectsValidSharedSecretWithForwardedIdentity()
    {
        using var server = BuildServer(SettingsFor(), isOrchestrator: true);
        using var client = server.CreateClient();

        var response = await client.SendAsync(Forged(Secret));

        response.StatusCode.Should().Be(HttpStatusCode.Unauthorized);
    }

    [Fact]
    public async Task Orchestrator_StillAuthenticatesBearerTokens()
    {
        var settings = SettingsFor();
        var token = new JwtTokenService(settings).IssueAccessToken("alice@example.com", "customer");
        using var server = BuildServer(settings, isOrchestrator: true);
        using var client = server.CreateClient();

        var request = new HttpRequestMessage(HttpMethod.Post, "/api/chat");
        request.Headers.Add("Authorization", $"Bearer {token}");
        var response = await client.SendAsync(request);

        response.StatusCode.Should().Be(HttpStatusCode.OK);
        var body = await response.Content.ReadFromJsonAsync<JsonElement>();
        body.GetProperty("email").GetString().Should().Be("alice@example.com");
        body.GetProperty("role").GetString().Should().Be("customer");
    }

    [Fact]
    public async Task Specialist_AcceptsSharedSecretWithForwardedIdentity()
    {
        using var server = BuildServer(SettingsFor(), isOrchestrator: false);
        using var client = server.CreateClient();

        var response = await client.SendAsync(Forged(Secret));

        response.StatusCode.Should().Be(HttpStatusCode.OK);
        var body = await response.Content.ReadFromJsonAsync<JsonElement>();
        body.GetProperty("email").GetString().Should().Be("admin@example.com");
        body.GetProperty("role").GetString().Should().Be("admin");
    }

    [Theory]
    [InlineData("wrong-secret")]
    [InlineData("ssssssssssssssssssssssssssssssssssssssssssssssss-longer")]
    public async Task Specialist_RejectsWrongSharedSecret(string secret)
    {
        using var server = BuildServer(SettingsFor(), isOrchestrator: false);
        using var client = server.CreateClient();

        var response = await client.SendAsync(Forged(secret));

        response.StatusCode.Should().Be(HttpStatusCode.Unauthorized);
    }

    [Fact]
    public void ComposePlaceholderJwtSecret_IsFlaggedUnsafe()
    {
        var settings = new AgentSettings
        {
            AgentSharedSecret = Secret,
            JwtSecret = "change-me-in-production-use-random-256-bit-key",
            Environment = "production",
        };

        var act = () => AgentSettingsValidator.Validate(settings, NullLogger.Instance);

        act.Should().Throw<InvalidOperationException>().WithMessage("*JWT_SECRET*placeholder default*");
    }
}
