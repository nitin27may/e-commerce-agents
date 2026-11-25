using ECommerceAgents.Shared.Agents;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Prompts;
using FluentAssertions;
using Microsoft.Extensions.AI;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// Per-agent model selection (P-2): the .NET twin of <c>tests/test_model_override.py</c>. No shipped
/// YAML carries a <c>model:</c> block, so each test copies the real prompt tree to a temp directory.
/// </summary>
// Creates chat clients with real agent names, which writes GatewayHeaders' process-wide last-agent value that the
// gateway-header tests read (#333); running in the same collection keeps them from overlapping.
[Collection(nameof(ProcessEnvironmentCollection))]
public sealed class ModelOverrideTests : IDisposable
{
    private readonly string _root = Path.Combine(Path.GetTempPath(), "model-override-" + Guid.NewGuid().ToString("N"));

    public ModelOverrideTests()
    {
        CopyDir(RealPromptsRoot(), _root);
    }

    public void Dispose() => Directory.Delete(_root, recursive: true);

    private static string RealPromptsRoot()
    {
        var dir = new DirectoryInfo(Directory.GetCurrentDirectory());
        while (dir is not null && !Directory.Exists(Path.Combine(dir.FullName, "agents", "python", "config", "prompts")))
        {
            dir = dir.Parent;
        }

        dir.Should().NotBeNull();
        return Path.Combine(dir!.FullName, "agents", "python", "config", "prompts");
    }

    private static void CopyDir(string from, string to)
    {
        Directory.CreateDirectory(to);
        foreach (var f in Directory.GetFiles(from))
        {
            File.Copy(f, Path.Combine(to, Path.GetFileName(f)));
        }

        foreach (var d in Directory.GetDirectories(from))
        {
            CopyDir(d, Path.Combine(to, Path.GetFileName(d)));
        }
    }

    private void AddBlock(string agent, string block) =>
        File.AppendAllText(Path.Combine(_root, $"{agent}.yaml"), "\nmodel:\n" + block);

    private static AgentSettings OpenAi() => new()
    {
        LlmProvider = "openai",
        OpenAiApiKey = "sk-test",
        LlmModel = "gpt-4.1",
    };

    [Fact]
    public void NoShippedPromptFileCarriesAModelBlock()
    {
        var real = new PromptLoader(RealPromptsRoot());
        real.ValidateModelOverrides();
        foreach (var path in Directory.GetFiles(RealPromptsRoot(), "*.yaml"))
        {
            real.LoadModelOverride(Path.GetFileNameWithoutExtension(path)).Should().BeNull(path);
        }
    }

    [Theory]
    [InlineData("orchestrator")]
    [InlineData("product-discovery")]
    [InlineData("handoff-triage")]
    public void ModelBlockNeverChangesTheComposedPrompt(string agent)
    {
        var loader = new PromptLoader(_root);
        var before = loader.Load(agent, "admin");
        before.Should().NotBeEmpty();

        AddBlock(agent, "  provider: openai\n  model: gpt-4.1-mini\n  temperature: 0.1\n");

        var after = loader.Load(agent, "admin");
        System.Text.Encoding.UTF8.GetBytes(after).Should().Equal(System.Text.Encoding.UTF8.GetBytes(before));
        loader.LoadModelOverride(agent).Should().Be(new ModelOverride("openai", "gpt-4.1-mini", 0.1));
    }

    [Theory]
    [InlineData("  provider: gemini\n  model: x\n", "unknown model.provider")]
    [InlineData("  model: \"\"\n", "non-empty string")]
    [InlineData("  temperature: 3\n", "between 0 and 2")]
    [InlineData("  temperature: hot\n", "between 0 and 2")]
    [InlineData("  modle: x\n", "unknown key")]
    public void InvalidBlockThrowsNamingTheFile(string block, string fragment)
    {
        AddBlock("pricing-promotions", block);
        var loader = new PromptLoader(_root);

        var load = () => loader.LoadModelOverride("pricing-promotions");
        load.Should().Throw<InvalidOperationException>().WithMessage($"pricing-promotions.yaml*{fragment}*");
        var validate = () => loader.ValidateModelOverrides();
        validate.Should().Throw<InvalidOperationException>().WithMessage("pricing-promotions.yaml*");
    }

    [Fact]
    public void ApplyTo_ReplacesProviderAndModelOnly()
    {
        var s = new ModelOverride("azure", "mini-dep", null).ApplyTo(OpenAi() with
        {
            AzureOpenAiEndpoint = "https://x.openai.azure.com/",
            AzureOpenAiKey = "k",
        });
        s.LlmProvider.Should().Be("azure");
        s.AzureOpenAiDeployment.Should().Be("mini-dep");
        s.LlmModel.Should().Be("gpt-4.1");
    }

    [Fact]
    public void ApplyTo_AnthropicDefaultsToTheAnthropicModelAndOpenAiFromAnthropicNeedsAModel()
    {
        var s = new ModelOverride("anthropic", null, null).ApplyTo(OpenAi() with { AnthropicApiKey = "k" });
        (s.LlmProvider, s.LlmModel).Should().Be(("anthropic", "claude-sonnet-5-5"));

        var global = OpenAi() with { LlmProvider = "anthropic", LlmModel = "claude-sonnet-5-5" };
        var act = () => new ModelOverride("openai", null, null).ApplyTo(global);
        act.Should().Throw<InvalidOperationException>().WithMessage("model.model is required*");
        new ModelOverride("openai", "gpt-4.1-mini", null).ApplyTo(global).LlmModel.Should().Be("gpt-4.1-mini");
    }

    [Fact]
    public void ApplyTo_IsIgnoredUnderReplay()
    {
        var replay = new AgentSettings { LlmProvider = "replay" };
        new ModelOverride("openai", "gpt-4.1-nano", 0.5).ApplyTo(replay).Should().BeSameAs(replay);
    }

    [Fact]
    public void Create_UsesTheAgentsModelAndFallsBackToGlobalSettings()
    {
        AddBlock("orchestrator", "  model: gpt-4.1-nano\n  temperature: 0\n");
        var loader = new PromptLoader(_root);

        ModelOf(ChatClientFactory.Create(OpenAi(), "orchestrator", loader)).Should().Be("gpt-4.1-nano");
        ModelOf(ChatClientFactory.Create(OpenAi(), "product-discovery", loader)).Should().Be("gpt-4.1");
        ModelOf(ChatClientFactory.Create(OpenAi())).Should().Be("gpt-4.1");
        ModelOf(ChatClientFactory.Create(OpenAi(), "orchestrator", null)).Should().Be("gpt-4.1");
    }

    [Fact]
    public void Create_InvalidBlockFailsNamingTheFile()
    {
        AddBlock("orchestrator", "  provider: bogus\n");
        var act = () => ChatClientFactory.Create(OpenAi(), "orchestrator", new PromptLoader(_root));
        act.Should().Throw<InvalidOperationException>().WithMessage("orchestrator.yaml*");
    }

    private static string? ModelOf(IChatClient client) =>
        client.GetService<ChatClientMetadata>()?.DefaultModelId;
}
