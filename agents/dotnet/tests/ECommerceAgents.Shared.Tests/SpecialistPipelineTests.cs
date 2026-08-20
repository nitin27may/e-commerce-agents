using ECommerceAgents.Shared.Agents;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.ContextProviders;
using ECommerceAgents.Shared.Middleware;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging.Abstractions;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// Covers issue #12: <see cref="AgentRunLogger"/>, <see cref="ToolAuditMiddleware"/>
/// and <see cref="PiiRedactor"/> previously existed but were attached to no
/// <see cref="AIAgent"/> in production — only exercised directly by
/// <c>MiddlewareTests</c>. These tests exercise them through the real
/// <see cref="AIAgentBuilder"/> pipeline <see cref="SpecialistPipeline.Apply"/>
/// composes, the same wiring <see cref="SpecialistAgentFactory.Create"/> now
/// applies whenever a <see cref="IServiceProvider"/> is supplied.
/// </summary>
public sealed class SpecialistPipelineTests
{
    private static IServiceProvider BuildServices()
    {
        var services = new ServiceCollection();
        services.AddSingleton<Microsoft.Extensions.Logging.ILoggerFactory>(NullLoggerFactory.Instance);
        services.AddLogging();
        services.AddSingleton<AgentRunLogger>();
        services.AddSingleton<ToolAuditMiddleware>();
        services.AddSingleton<PiiRedactor>();
        return services.BuildServiceProvider();
    }

    [Fact]
    public async Task Apply_RedactsCardNumbersInInboundMessages_BeforeTheChatClientSeesThem()
    {
        var services = BuildServices();
        var fakeChatClient = new FakeChatClient().EnqueueResponse("ok, thanks");
        var inner = fakeChatClient.AsAIAgent(instructions: "be helpful", name: "test-agent");

        var wrapped = SpecialistPipeline.Apply(inner, new AgentSettings(), services);
        await wrapped.RunAsync("My card is 4111 1111 1111 1111, please charge it.");

        fakeChatClient.ReceivedMessages.Should().HaveCount(1);
        var received = fakeChatClient.ReceivedMessages[0].Single(m => m.Role == ChatRole.User);
        received.Text.Should().Contain(PiiRedactor.CardMask);
        received.Text.Should().NotContain("4111 1111 1111 1111");
    }

    [Fact]
    public async Task Apply_LeavesNonPiiMessagesUnchanged()
    {
        var services = BuildServices();
        var fakeChatClient = new FakeChatClient().EnqueueResponse("sure");
        var inner = fakeChatClient.AsAIAgent(instructions: "be helpful", name: "test-agent");

        var wrapped = SpecialistPipeline.Apply(inner, new AgentSettings(), services);
        await wrapped.RunAsync("What's the status of my order?");

        var received = fakeChatClient.ReceivedMessages[0].Single(m => m.Role == ChatRole.User);
        received.Text.Should().Be("What's the status of my order?");
    }

    [Fact]
    public async Task Apply_StillProducesTheChatClientsResponse()
    {
        var services = BuildServices();
        var fakeChatClient = new FakeChatClient().EnqueueResponse("the pipeline did not swallow this");
        var inner = fakeChatClient.AsAIAgent(instructions: "be helpful", name: "test-agent");

        var wrapped = SpecialistPipeline.Apply(inner, new AgentSettings(), services);
        var response = await wrapped.RunAsync("hi");

        response.Text.Should().Be("the pipeline did not swallow this");
    }
}

/// <summary>
/// <see cref="EcommerceContextProvider"/> — the adapter making
/// <see cref="ContextEnricher"/> (previously wired to nothing in production,
/// see issue #12) attachable via
/// <see cref="ChatClientAgentOptions.AIContextProviders"/>. Only the
/// no-identity short-circuit is covered here without a real Postgres
/// connection; the enrichment path itself is already covered against a real
/// database by <c>ContextEnricherTests</c>.
/// </summary>
public sealed class EcommerceContextProviderTests
{
    [Fact]
    public async Task InvokingAsync_ReturnsEmptyContext_WhenNoUserIsAuthenticated()
    {
        Context.RequestContext.CurrentUserEmail = string.Empty;
        var provider = new EcommerceContextProvider(new ContextEnricher(null!));

        var fakeChatClient = new FakeChatClient().EnqueueResponse("ok");
        var inner = fakeChatClient.AsAIAgent(
            new ChatClientAgentOptions
            {
                Name = "test-agent",
                ChatOptions = new ChatOptions { Instructions = "be helpful" },
                AIContextProviders = [provider],
            }
        );

        // Would throw a NullReferenceException from ContextEnricher.EnrichAsync
        // (constructed above with a null DatabasePool) if the empty-email
        // short-circuit in EcommerceContextProvider.InvokingCoreAsync didn't
        // fire before reaching the enricher.
        var response = await inner.RunAsync("hi");

        response.Text.Should().Be("ok");
    }
}
