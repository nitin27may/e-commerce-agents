// MAF v1 — Chapter 04 tests (Sessions and Memory)

using FluentAssertions;
using Xunit;

namespace MafV1.Ch04.Sessions.Tests;

public sealed class SessionsTests
{
    static SessionsTests() => LoadRepoEnv();

    [Fact]
    [Trait("Category", "Integration")]
    public async Task Session_Persists_Across_Fresh_Agent_Instances()
    {
        if (!LlmCredentialsPresent())
        {
            Console.WriteLine("[skip] no LLM credentials in .env");
            return;
        }

        var sessionPath = Path.Combine(Path.GetTempPath(), $"maf-v1-ch04-{Guid.NewGuid():N}.json");
        try
        {
            var agent1 = Program.BuildAgent();
            await Program.AskAndSave(agent1, "Remember: my favorite color is teal.", sessionPath);
            File.Exists(sessionPath).Should().BeTrue();

            // Fresh agent instance (what a separate CLI run would produce).
            var agent2 = Program.BuildAgent();
            var (answer, _) = await Program.AskAndSave(
                agent2,
                "What color did I tell you I liked? Answer with only the color.",
                sessionPath);

            answer.ToLowerInvariant().Should().Contain("teal",
                "the second agent instance must see turn 1 via the persisted session");
        }
        finally
        {
            if (File.Exists(sessionPath)) File.Delete(sessionPath);
        }
    }

    [Fact]
    [Trait("Category", "Integration")]
    public async Task Missing_Session_File_Starts_A_Fresh_Conversation()
    {
        if (!LlmCredentialsPresent())
        {
            Console.WriteLine("[skip] no LLM credentials in .env");
            return;
        }

        var nonexistent = Path.Combine(Path.GetTempPath(), $"never-written-{Guid.NewGuid():N}.json");
        try
        {
            var agent = Program.BuildAgent();
            var (answer, path) = await Program.AskAndSave(
                agent,
                "Hello! Respond with just 'hi'.",
                nonexistent);

            File.Exists(path).Should().BeTrue("save should create the file on first run");
            answer.Should().NotBeNullOrWhiteSpace();
        }
        finally
        {
            if (File.Exists(nonexistent)) File.Delete(nonexistent);
        }
    }

    [Fact]
    public async Task LoadOrNew_Returns_Fresh_Session_When_File_Missing()
    {
        if (!LlmCredentialsPresent())
        {
            Console.WriteLine("[skip] no LLM credentials in .env");
            return;
        }

        var nonexistent = Path.Combine(Path.GetTempPath(), $"missing-{Guid.NewGuid():N}.json");
        var agent = Program.BuildAgent();
        var session = await Program.LoadOrNew(agent, nonexistent);
        session.Should().NotBeNull();
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
