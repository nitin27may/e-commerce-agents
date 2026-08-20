// MAF v1 — Chapter 02 tests (Adding Tools)

using FluentAssertions;
using Xunit;

namespace MafV1.Ch02.AddTools.Tests;

public sealed class AddToolsTests
{
    static AddToolsTests() => LoadRepoEnv();

    // ─────────── Unit tests (tool function directly) ───────────

    [Fact]
    public void Product_Price_Tool_Returns_Canned_Data_For_Known_Sku()
    {
        Program.GetProductPrice("SKU-001").Should().Contain("79.99").And.Contain("Wireless Mouse");
    }

    [Fact]
    public void Product_Price_Tool_Is_Case_Insensitive()
    {
        Program.GetProductPrice("sku-001").Should().Be(Program.GetProductPrice("SKU-001"));
    }

    [Fact]
    public void Product_Price_Tool_Handles_Unknown_Sku()
    {
        Program.GetProductPrice("SKU-999").Should().Contain("No pricing data");
    }

    // ─────────── Integration (hits real LLM) ───────────

    [Fact]
    [Trait("Category", "Integration")]
    public async Task Real_LLM_Invokes_Product_Price_Tool()
    {
        if (!LlmCredentialsPresent())
        {
            Console.WriteLine("[skip] no LLM credentials in .env");
            return;
        }

        var agent = Program.BuildAgent();
        var answer = (await Program.Ask(agent, "What's the price of SKU-001?")).ToLowerInvariant();
        answer.Should().Match(a => a.Contains("79.99") || a.Contains("wireless mouse"),
            "canned product-price data should reach the final answer when the tool is called");
    }

    [Fact]
    [Trait("Category", "Integration")]
    public async Task Real_LLM_Skips_Tool_For_Unrelated_Question()
    {
        if (!LlmCredentialsPresent())
        {
            Console.WriteLine("[skip] no LLM credentials in .env");
            return;
        }

        var agent = Program.BuildAgent();
        var answer = (await Program.Ask(
            agent,
            "What is the capital of France? Answer with only the city name.")).ToLowerInvariant();

        answer.Should().Contain("paris");
        answer.Should().NotContain("79.99",
            "canned product-price data must not bleed into a non-price answer");
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
