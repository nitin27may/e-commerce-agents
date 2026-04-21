// MAF v1 — Chapter 08 tests (MCP Tools)

using FluentAssertions;
using Xunit;

namespace MafV1.Ch08.McpTools.Tests;

public sealed class McpToolsTests
{
    static McpToolsTests() => LoadRepoEnv();

    [Fact]
    [Trait("Category", "Integration")]
    public async Task Real_LLM_Calls_Mcp_Weather_Tool()
    {
        if (!LlmCredentialsPresent()) { Console.WriteLine("[skip]"); return; }

        var answer = await Program.Run("What's the weather in Paris?");
        var lowered = answer.ToLowerInvariant();
        (lowered.Contains("sunny") || lowered.Contains("18")).Should().BeTrue(
            $"expected MCP-sourced weather text in answer, got: {answer}");
    }

    [Fact]
    [Trait("Category", "Integration")]
    public async Task Real_LLM_Skips_Mcp_Tool_For_Unrelated_Question()
    {
        if (!LlmCredentialsPresent()) { Console.WriteLine("[skip]"); return; }

        var answer = await Program.Run("What is the capital of France? Answer with only the city name.");
        answer.ToLowerInvariant().Should().Contain("paris");
        answer.ToLowerInvariant().Should().NotContain("sunny, 18");
    }

    [Fact]
    [Trait("Category", "Integration")]
    public async Task Mcp_Client_Discovers_Weather_Tool()
    {
        if (!LlmCredentialsPresent()) { Console.WriteLine("[skip]"); return; }

        await using var mcp = await Program.BuildMcpClientAsync();
        var tools = await mcp.ListToolsAsync();
        tools.Select(t => t.Name).Should().Contain("get_weather");
    }

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
