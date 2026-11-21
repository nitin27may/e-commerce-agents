using System.ClientModel.Primitives;
using System.Net;
using System.Text;
using Azure.AI.OpenAI;
using Azure.Core;
using Azure.Identity;
using ECommerceAgents.Shared.Agents;
using ECommerceAgents.Shared.Configuration;
using FluentAssertions;
using Microsoft.Extensions.Configuration;

using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// <c>AZURE_OPENAI_AUTH=key|identity</c> (#89 P3): identity mode sends a bearer token from the
/// credential and never a key. Python parity: <c>tests/test_azure_identity_auth.py</c>.
/// </summary>
[Collection(nameof(ProcessEnvironmentCollection))]
public sealed class AzureIdentityAuthTests
{
    private sealed class FakeCredential : TokenCredential
    {
        public int Calls { get; private set; }

        public override AccessToken GetToken(TokenRequestContext requestContext, CancellationToken cancellationToken)
        {
            Calls++;
            return new AccessToken("fake-entra-token", DateTimeOffset.UtcNow.AddHours(1));
        }

        public override ValueTask<AccessToken> GetTokenAsync(TokenRequestContext requestContext, CancellationToken cancellationToken) =>
            new(GetToken(requestContext, cancellationToken));
    }

    private sealed class RecordingHandler : HttpMessageHandler
    {
        public List<HttpRequestMessage> Requests { get; } = [];

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            Requests.Add(request);
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.BadRequest)
            {
                Content = new StringContent("{\"error\":{\"message\":\"stop\"}}", Encoding.UTF8, "application/json"),
            });
        }
    }

    private static AgentSettings Settings(string auth, string key = "") => new()
    {
        LlmProvider = "azure",
        AzureOpenAiEndpoint = "https://x.openai.azure.com/",
        AzureOpenAiDeployment = "d",
        AzureOpenAiKey = key,
        AzureOpenAiAuth = auth,
        EmbeddingProvider = "azure",
    };

    private static (AzureOpenAIClientOptions Options, RecordingHandler Handler) StubTransport()
    {
        var handler = new RecordingHandler();
        var options = new AzureOpenAIClientOptions
        {
            Transport = new HttpClientPipelineTransport(new HttpClient(handler)),
            RetryPolicy = new ClientRetryPolicy(0),
        };
        return (options, handler);
    }

    [Fact]
    public void Loader_DefaultsToKey_AndValidates()
    {
        AgentSettingsLoader.Load(new ConfigurationBuilder().Build()).AzureOpenAiAuth.Should().Be("key");

        AgentSettings Load(string raw) => AgentSettingsLoader.Load(new ConfigurationBuilder()
            .AddInMemoryCollection(new Dictionary<string, string?> { ["AZURE_OPENAI_AUTH"] = raw, ["AZURE_CLIENT_ID"] = " abc " })
            .Build());

        Load(" Identity ").AzureOpenAiAuth.Should().Be("identity");
        Load(" Identity ").AzureClientId.Should().Be("abc");
        Load("").AzureOpenAiAuth.Should().Be("key");
        var act = () => Load("password");
        act.Should().Throw<InvalidOperationException>().WithMessage("*AZURE_OPENAI_AUTH*");
    }

    [Theory]
    [InlineData("key", "", false)]
    [InlineData("key", "k", true)]
    [InlineData("identity", "", true)]
    [InlineData("identity", "k", true)]
    public void ValidatorMatrix_KeyModeNeedsKey_IdentityModeNeedsNone(string auth, string key, bool ok)
    {
        var (options, _) = StubTransport();
        var act = () => ChatClientFactory.CreateChatClient(Settings(auth, key), new FakeCredential(), options);

        if (ok)
        {
            act.Should().NotThrow();
        }
        else
        {
            act.Should().Throw<InvalidOperationException>().WithMessage("*AZURE_OPENAI_KEY*");
        }
    }

    [Fact]
    public void Chat_IdentityMode_BuildsWithACredentialAndNoKey_WithoutTouchingIt()
    {
        // Construction only: sending a chat request through the stub transport hits a pre-existing
        // Azure.AI.OpenAI 2.1.0 / OpenAI package mismatch (MissingMethodException in the chat serializer),
        // which is independent of this change. The embeddings tests below cover the wire.
        var credential = new FakeCredential();
        var (options, handler) = StubTransport();

        var chat = ChatClientFactory.CreateChatClient(Settings("identity"), credential, options);

        chat.Should().NotBeNull();
        credential.Calls.Should().Be(0);
        handler.Requests.Should().BeEmpty();
    }

    [Fact]
    public async Task KeyMode_EmbeddingsSendApiKey_AndNeverCallTheCredential()
    {
        var credential = new FakeCredential();
        var (options, handler) = StubTransport();
        var embeddings = EmbeddingClientFactory.CreateEmbeddingClient(Settings("key", "the-key"), credential, options);

        var act = async () => await embeddings.GenerateEmbeddingAsync("x");
        await act.Should().ThrowAsync<Exception>();

        credential.Calls.Should().Be(0);
        handler.Requests.Single().Headers.GetValues("api-key").Should().ContainSingle("the-key");
    }

    [Fact]
    public async Task IdentityMode_EmbeddingsSendBearerToken_AndNoKey()
    {
        var credential = new FakeCredential();
        var (options, handler) = StubTransport();
        var embeddings = EmbeddingClientFactory.CreateEmbeddingClient(Settings("identity"), credential, options);

        var act = async () => await embeddings.GenerateEmbeddingAsync("x");
        await act.Should().ThrowAsync<Exception>();

        var request = handler.Requests.Should().ContainSingle().Subject;
        request.Headers.Authorization!.ToString().Should().Be("Bearer fake-entra-token");
        request.Headers.Contains("api-key").Should().BeFalse();
    }

    [Fact]
    public void KeyMode_EmbeddingsStillNeedAKey()
    {
        var act = () => EmbeddingClientFactory.CreateEmbeddingClient(Settings("key"));
        act.Should().Throw<InvalidOperationException>().WithMessage("*AZURE_OPENAI_KEY*");
    }

    [Fact]
    public void CredentialSelection_UserAssignedWhenClientIdIsSet()
    {
        AzureIdentity.CreateCredential(Settings("identity")).Should().BeOfType<DefaultAzureCredential>();
        AzureIdentity.CreateCredential(Settings("identity") with { AzureClientId = "11111111-2222-3333-4444-555555555555" })
            .Should().BeOfType<ManagedIdentityCredential>();
    }
}
