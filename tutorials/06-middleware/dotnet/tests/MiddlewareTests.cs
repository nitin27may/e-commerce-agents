// MAF v1 — Chapter 06 tests (Middleware)

using FluentAssertions;
using Xunit;

namespace MafV1.Ch06.Middleware.Tests;

public sealed class MiddlewareTests
{
    static MiddlewareTests() => LoadRepoEnv();

    [Fact]
    [Trait("Category", "Integration")]
    public async Task Weather_Tool_Invocations_Observed_By_Function_Middleware()
    {
        if (!LlmCredentialsPresent()) { Console.WriteLine("[skip]"); return; }

        var stats = new Program.Stats();
        var agent = Program.BuildAgent(stats);
        await agent.RunAsync("What's the weather in Paris?");

        stats.ToolInvocations.Should().Contain("Paris");
    }

    [Fact]
    [Trait("Category", "Integration")]
    public async Task Chat_Middleware_Redacts_Card_Numbers_Before_Llm()
    {
        if (!LlmCredentialsPresent()) { Console.WriteLine("[skip]"); return; }

        var stats = new Program.Stats();
        var agent = Program.BuildAgent(stats);
        await agent.RunAsync("My card is 4111-1111-1111-1111. What's the weather in Paris?");

        stats.PiiRedactions.Should().BeGreaterOrEqualTo(1,
            "the card number must be redacted before reaching the provider");
    }

    [Fact]
    [Trait("Category", "Integration")]
    public async Task Chat_Middleware_Does_Not_Redact_Clean_Messages()
    {
        if (!LlmCredentialsPresent()) { Console.WriteLine("[skip]"); return; }

        var stats = new Program.Stats();
        var agent = Program.BuildAgent(stats);
        await agent.RunAsync("What's the weather in Tokyo?");

        stats.PiiRedactions.Should().Be(0);
    }

    [Fact]
    [Trait("Category", "Integration")]
    public async Task Runs_Do_Not_Leak_State_Between_Agents()
    {
        if (!LlmCredentialsPresent()) { Console.WriteLine("[skip]"); return; }

        var a = new Program.Stats();
        var b = new Program.Stats();
        var agentA = Program.BuildAgent(a);
        var agentB = Program.BuildAgent(b);

        await agentA.RunAsync("What's the weather in Paris?");
        await agentB.RunAsync("What's the weather in Tokyo?");

        a.ToolInvocations.Should().NotContain("Tokyo");
        b.ToolInvocations.Should().NotContain("Paris");
    }

    // ─────────── Helpers ───────────

    private static bool LlmCredentialsPresent()
    {
        var provider = Environment.GetEnvironmentVariable("LLM_PROVIDER")?.ToLowerInvariant() ?? "openai";
        if (provider == "azure")
        {
            var key = Environment.GetEnvironmentVariable("AZURE_OPENAI_KEY")
                      ?? Environment.GetEnvironmentVariable("AZURE_OPENAI_API_KEY");
            var endpoint = Environment.GetEnvironmentVariable("AZURE_OPENAI_ENDPOINT");
            return !string.IsNullOrWhiteSpace(key) && !string.IsNullOrWhiteSpace(endpoint);
        }

        var openAiKey = Environment.GetEnvironmentVariable("OPENAI_API_KEY");
        return !string.IsNullOrWhiteSpace(openAiKey) && !openAiKey.StartsWith("sk-your-", StringComparison.Ordinal);
    }

    private static void LoadRepoEnv()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !File.Exists(Path.Combine(dir.FullName, ".env")))
        {
            dir = dir.Parent;
        }
        if (dir is null) return;

        foreach (var raw in File.ReadAllLines(Path.Combine(dir.FullName, ".env")))
        {
            var line = raw.Trim();
            if (line.Length == 0 || line.StartsWith('#')) continue;
            var eq = line.IndexOf('=');
            if (eq < 0) continue;
            var key = line[..eq].Trim();
            var value = line[(eq + 1)..].Trim().Trim('"').Trim('\'');
            if (Environment.GetEnvironmentVariable(key) is null)
            {
                Environment.SetEnvironmentVariable(key, value);
            }
        }
    }
}
