using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Logging;
using FluentAssertions;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>The startup line carries facts, never a secret. Python twin: tests/test_startup_info.py.</summary>
public sealed class StartupLogTests
{
    private static string? NoEnv(string _) => null;

    [Fact]
    public void LocalOpenAiLineNamesTheHostAndNeverTheKeyOrPath()
    {
        var settings = new AgentSettings
        {
            LlmProvider = "openai",
            LlmModel = "qwen2.5:14b",
            LlmBaseUrl = "http://user:hunter2@localhost:11434/v1?token=abc",
            OpenAiApiKey = "sk-super-secret-value",
            EmbeddingProvider = "openai_compatible",
            EmbeddingModel = "nomic-embed-text",
        };

        var line = StartupLog.Format(StartupLog.Summary("orchestrator", settings, NoEnv));

        line.Should().Contain("provider=openai").And.Contain("model=qwen2.5:14b")
            .And.Contain("llm_host=localhost:11434").And.Contain("llm_api=chat_completions")
            .And.Contain("embedding_provider=openai_compatible").And.Contain("embedding_model=nomic-embed-text")
            .And.Contain("stack=dotnet").And.Contain("profile=none");
        line.Should().NotContain("hunter2").And.NotContain("sk-super-secret-value")
            .And.NotContain("token=abc").And.NotContain("/v1");
    }

    [Fact]
    public void AzureUsesTheDeploymentAndAnthropicItsOwnModel()
    {
        var azure = StartupLog.Summary("a", new AgentSettings { LlmProvider = "azure", AzureOpenAiDeployment = "gpt-5.1" }, NoEnv);
        azure["model"].Should().Be("gpt-5.1");
        azure["llm_host"].Should().BeEmpty();

        var claude = StartupLog.Summary("a", new AgentSettings { LlmProvider = "anthropic", AnthropicModel = "claude-sonnet-5-5" }, NoEnv);
        claude["model"].Should().Be("claude-sonnet-5-5");
    }

    [Theory]
    [InlineData(null, null, "shipped")]
    [InlineData("/pricing/models.yaml", null, "file:models.yaml")]
    [InlineData(null, "/pricing/overrides.yaml", "shipped+overrides:overrides.yaml")]
    public void PricingSourceNamesFilesNotDirectories(string? file, string? overrides, string expected) =>
        StartupLog.PricingSource(file, overrides).Should().Be(expected);

    [Fact]
    public void ProfileComesFromTheEnvironment()
    {
        var summary = StartupLog.Summary("a", new AgentSettings(), name => name == "RUN_PROFILE" ? "ollama" : null);
        summary["profile"].Should().Be("ollama");
    }

    [Fact]
    public void AnInvalidEmbeddingProviderIsReportedNotThrown()
    {
        var summary = StartupLog.Summary("a", new AgentSettings { EmbeddingProvider = "bogus" }, NoEnv);
        summary["embedding_provider"].Should().Be("invalid");
    }
}
