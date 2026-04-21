// MAF v1 — Chapter 05 tests (Context Providers)

using FluentAssertions;
using Microsoft.Agents.AI;
using Xunit;
using static MafV1.Ch05.ContextProviders.Program;

namespace MafV1.Ch05.ContextProviders.Tests;

public sealed class ContextProvidersTests
{
    static ContextProvidersTests() => LoadRepoEnv();

    // ─────────── Unit tests (no LLM) ───────────

    [Fact]
    public void Provider_Stores_Injected_User_Fields()
    {
        var provider = new UserProfileProvider("alice@example.com", "Alice", "gold");
        provider.Email.Should().Be("alice@example.com");
        provider.Name.Should().Be("Alice");
        provider.LoyaltyTier.Should().Be("gold");
    }

    [Fact]
    public void Different_Users_Produce_Different_Provider_State()
    {
        var alice = new UserProfileProvider("alice@example.com", "Alice", "gold");
        var bob = new UserProfileProvider("bob@example.com", "Bob", "silver");

        alice.Name.Should().Be("Alice");
        alice.LoyaltyTier.Should().Be("gold");
        bob.Name.Should().Be("Bob");
        bob.LoyaltyTier.Should().Be("silver");
    }

    [Fact]
    public void BuildAgent_Accepts_Custom_Provider_Without_Error()
    {
        if (!LlmCredentialsPresent())
        {
            Console.WriteLine("[skip] no LLM credentials in .env");
            return;
        }

        var provider = new UserProfileProvider("carol@example.com", "Carol", "platinum");
        var agent = BuildAgent(provider);
        agent.Should().NotBeNull();
        agent.Name.Should().Be("personalized-agent");
    }

    // ─────────── Integration (hits real LLM) ───────────

    [Fact]
    [Trait("Category", "Integration")]
    public async Task Real_LLM_Greets_User_By_Injected_Name_And_Tier()
    {
        if (!LlmCredentialsPresent())
        {
            Console.WriteLine("[skip] no LLM credentials in .env");
            return;
        }

        var agent = BuildAgent(new UserProfileProvider("alice@example.com", "Alice", "gold"));
        var response = await agent.RunAsync("Greet me by name and tell me my loyalty tier.");

        response.Text.ToLowerInvariant().Should().Contain("alice");
        response.Text.ToLowerInvariant().Should().Contain("gold");
    }

    [Fact]
    [Trait("Category", "Integration")]
    public async Task Real_LLM_Per_User_Isolation()
    {
        if (!LlmCredentialsPresent())
        {
            Console.WriteLine("[skip] no LLM credentials in .env");
            return;
        }

        var aliceAgent = BuildAgent(new UserProfileProvider("alice@example.com", "Alice", "gold"));
        var bobAgent = BuildAgent(new UserProfileProvider("bob@example.com", "Bob", "silver"));

        var aliceReply = (await aliceAgent.RunAsync("What's my name?")).Text.ToLowerInvariant();
        var bobReply = (await bobAgent.RunAsync("What's my name?")).Text.ToLowerInvariant();

        aliceReply.Should().Contain("alice");
        bobReply.Should().Contain("bob");
        aliceReply.Should().NotContain("bob");
        bobReply.Should().NotContain("alice");
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
