// MAF v1 — Chapter 03 tests (Streaming + Multi-turn)
//
// Only integration tests here: streaming + session behavior is hard to fake
// without reimplementing large swaths of MAF internals, so tests rely on
// real Azure OpenAI via the repo-root .env. Skipped cleanly when absent.

using FluentAssertions;
using Xunit;

namespace MafV1.Ch03.Streaming.Tests;

public sealed class StreamingTests
{
    static StreamingTests() => LoadRepoEnv();

    [Fact]
    [Trait("Category", "Integration")]
    public async Task Real_LLM_Stream_Yields_Multiple_Chunks_For_Long_Answer()
    {
        if (!LlmCredentialsPresent()) { Console.WriteLine("[skip] no credentials"); return; }

        var agent = Program.BuildAgent();
        var session = await agent.CreateSessionAsync();
        var chunks = await Program.StreamAnswer(agent, "List 5 planets in the solar system.", session);

        chunks.Should().HaveCountGreaterThan(1,
            "a medium-length answer from OpenAI streams as several chunks");
        string.Concat(chunks).Should().NotBeNullOrWhiteSpace();
    }

    [Fact]
    [Trait("Category", "Integration")]
    public async Task Real_LLM_Multiturn_Preserves_Context_Across_Session()
    {
        if (!LlmCredentialsPresent()) { Console.WriteLine("[skip] no credentials"); return; }

        var agent = Program.BuildAgent();
        var perTurn = await Program.Chat(agent, new[]
        {
            "What is Python in one sentence?",
            "What year was it first released? Say only the year.",
        });

        perTurn.Should().HaveCount(2);
        var followUp = string.Concat(perTurn[1]);
        followUp.Should().Contain("1991",
            "the second turn must be able to resolve 'it' to Python from turn 1");
    }

    [Fact]
    [Trait("Category", "Integration")]
    public async Task Separate_Sessions_Do_Not_Share_Context()
    {
        if (!LlmCredentialsPresent()) { Console.WriteLine("[skip] no credentials"); return; }

        var agent = Program.BuildAgent();

        var s1 = await agent.CreateSessionAsync();
        await Program.StreamAnswer(agent, "Remember the word 'orange'.", s1);

        // Fresh session → the agent must not know about 'orange'.
        var s2 = await agent.CreateSessionAsync();
        var answer = string.Concat(
            await Program.StreamAnswer(agent, "What word did I ask you to remember? If none, say 'none'.", s2));

        answer.ToLowerInvariant().Should().Contain("none",
            "a new session must start with empty context");
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
