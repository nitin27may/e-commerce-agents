using ECommerceAgents.Shared.Agents;
using ECommerceAgents.Shared.Configuration;
using FluentAssertions;
using Microsoft.Extensions.Configuration;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// P-6: embeddings configured apart from the chat provider — the twin of <c>tests/test_embedding_decoupling.py</c>.
/// </summary>
public sealed class EmbeddingConfigTests
{
    private static AgentSettings Base() => new()
    {
        LlmProvider = "openai",
        OpenAiApiKey = "sk-test",
    };

    private static AgentSettings Load(Dictionary<string, string?> env) =>
        AgentSettingsLoader.Load(new ConfigurationBuilder().AddInMemoryCollection(env).Build());

    [Theory]
    [InlineData("openai")]
    [InlineData("azure")]
    [InlineData("replay")]
    [InlineData("anthropic")]
    public void UnsetEmbeddingProviderFollowsTheChatProvider(string chat) =>
        EmbeddingClientFactory.ResolveProvider(Base() with { LlmProvider = chat }).Should().Be(chat);

    [Fact]
    public void ExplicitEmbeddingProviderWinsAndUnknownIsRejected()
    {
        EmbeddingClientFactory.ResolveProvider(Base() with { EmbeddingProvider = "OpenAI_Compatible" })
            .Should().Be("openai_compatible");

        var act = () => EmbeddingClientFactory.ResolveProvider(Base() with { EmbeddingProvider = "cohere" });
        act.Should().Throw<InvalidOperationException>().WithMessage("Unknown EMBEDDING_PROVIDER*");
    }

    [Fact]
    public void Loader_ReadsTheNewSettingsAndDefaultsTo1536()
    {
        var defaults = Load([]);
        (defaults.EmbeddingProvider, defaults.EmbeddingBaseUrl, defaults.EmbeddingApiKey, defaults.EmbeddingDim)
            .Should().Be(("", "", "", 1536));

        var s = Load(new()
        {
            ["EMBEDDING_PROVIDER"] = " OpenAI_Compatible ",
            ["EMBEDDING_BASE_URL"] = "http://localhost:11434/v1",
            ["EMBEDDING_API_KEY"] = "k",
            ["EMBEDDING_MODEL"] = "nomic-embed-text",
            ["EMBEDDING_DIM"] = "768",
        });
        (s.EmbeddingProvider, s.EmbeddingBaseUrl, s.EmbeddingModel, s.EmbeddingDim)
            .Should().Be(("openai_compatible", "http://localhost:11434/v1", "nomic-embed-text", 768));
    }

    [Theory]
    [InlineData("0")]
    [InlineData("-4")]
    [InlineData("big")]
    public void Loader_RejectsABadDimension(string raw)
    {
        var act = () => Load(new() { ["EMBEDDING_DIM"] = raw });
        act.Should().Throw<InvalidOperationException>().WithMessage("EMBEDDING_DIM must be a positive integer");
    }

    [Fact]
    public void FollowingOpenAiKeepsTheChatBaseUrlAndUnavailableWithoutAKey()
    {
        EmbeddingClientFactory.CreateProvider(Base() with { LlmBaseUrl = "http://localhost:11434/v1" })
            .Should().BeOfType<OpenAIEmbeddingProvider>();
        EmbeddingClientFactory.CreateProvider(Base() with { OpenAiApiKey = "" })
            .Should().BeOfType<UnavailableEmbeddingProvider>();
    }

    [Fact]
    public void OpenAiCompatibleNeedsABaseUrlAndNoKey()
    {
        var local = Base() with { OpenAiApiKey = "", EmbeddingProvider = "openai_compatible" };
        EmbeddingClientFactory.CreateProvider(local).Should().BeOfType<UnavailableEmbeddingProvider>()
            .Which.Reason.Should().Contain("EMBEDDING_BASE_URL");

        EmbeddingClientFactory.CreateProvider(local with { EmbeddingBaseUrl = "http://localhost:11434/v1" })
            .Should().BeOfType<OpenAIEmbeddingProvider>();
    }

    [Fact]
    public async Task ClaudeChatCanUseSeparateEmbeddings()
    {
        var claude = Base() with { LlmProvider = "anthropic", AnthropicApiKey = "k" };

        var none = EmbeddingClientFactory.CreateProvider(claude);
        var act = () => none.EmbedAsVectorLiteralAsync("x");
        await act.Should().ThrowAsync<InvalidOperationException>().WithMessage("*no embeddings API*EMBEDDING_PROVIDER*");

        EmbeddingClientFactory.CreateProvider(claude with
        {
            EmbeddingProvider = "openai_compatible",
            EmbeddingBaseUrl = "http://localhost:11434/v1",
        }).Should().BeOfType<OpenAIEmbeddingProvider>();
    }

    [Fact]
    public void ModelNamePrefersTheAzureDeploymentOnlyForAzure()
    {
        var azure = Base() with { LlmProvider = "azure", AzureEmbeddingDeployment = "embed-dep" };
        EmbeddingClientFactory.EmbeddingModelName(azure).Should().Be("embed-dep");
        EmbeddingClientFactory.EmbeddingModelName(azure with { EmbeddingProvider = "openai" })
            .Should().Be("text-embedding-3-small");
        EmbeddingClientFactory.SupportsDimensions("text-embedding-3-large").Should().BeTrue();
        EmbeddingClientFactory.SupportsDimensions("nomic-embed-text").Should().BeFalse();
    }

    [Fact]
    public async Task ReplayWidthDefaultsTo1536AndFollowsEmbeddingDim()
    {
        ReplayEmbeddingProvider.Embed("noise cancelling headphones").Should().HaveCount(1536);
        ReplayEmbeddingProvider.Embed("noise cancelling headphones")
            .Should().Equal(ReplayEmbeddingProvider.Embed("noise cancelling headphones", 1536));

        var replay = EmbeddingClientFactory.CreateProvider(new AgentSettings { LlmProvider = "replay", EmbeddingDim = 768 });
        (await replay.EmbedAsVectorLiteralAsync("anything")).Split(',').Should().HaveCount(768);
    }

    [Fact]
    public void DimensionCheck_NamesEachMismatchedTableAndTheFix()
    {
        EmbeddingDimensionCheck.Problems(1536, new Dictionary<string, int?> { ["product_embeddings"] = 1536, ["agent_memories"] = 1536 })
            .Should().BeEmpty();
        // An unconstrained `vector` has no width to disagree with.
        EmbeddingDimensionCheck.Problems(1536, new Dictionary<string, int?> { ["product_embeddings"] = null }).Should().BeEmpty();

        var problems = EmbeddingDimensionCheck.Problems(
            768, new Dictionary<string, int?> { ["product_embeddings"] = 1536, ["agent_memories"] = 1536 });
        problems.Should().Equal("agent_memories.embedding is vector(1536)", "product_embeddings.embedding is vector(1536)");

        var message = EmbeddingDimensionCheck.Message(768, problems);
        message.Should().Contain("EMBEDDING_DIM=768").And.Contain("reset_embeddings.py").And.Contain("--dim 768");
    }
}
