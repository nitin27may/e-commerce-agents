// MAF v1 — Chapter 01 tests
//
// Two modes:
// - Unit tests use a stub IChatClient that returns canned responses.
// - Integration test hits the real LLM using keys from the repo-root .env;
//   it is skipped when the configured provider's key is missing.

using FluentAssertions;
using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;
using Xunit;

// The test stub works against Microsoft.Extensions.AI.IChatClient (the abstraction
// that MAF's ChatClientAgent accepts). No need for OpenAI-specific types here.

namespace MafV1.Ch01.FirstAgent.Tests;

public sealed class FirstAgentTests
{
    static FirstAgentTests() => LoadRepoEnv();

    // ─────────── Unit tests (no LLM) ───────────

    [Fact]
    public async Task Agent_Returns_Canned_Answer_From_Stub_Client()
    {
        var stub = new StubChatClient("Paris.");
        var agent = new ChatClientAgent(stub, instructions: Program.Instructions, name: "first-agent");

        var answer = await Program.Ask(agent, "What is the capital of France?");

        answer.Should().Be("Paris.");
        stub.CallCount.Should().Be(1);
    }

    [Fact]
    public async Task Agent_Forwards_User_Question_To_Chat_Client()
    {
        var stub = new StubChatClient("Ottawa.");
        var agent = new ChatClientAgent(stub, instructions: Program.Instructions, name: "first-agent");

        await Program.Ask(agent, "What is the capital of Canada?");

        var userMessages = stub.ReceivedMessages
            .SelectMany(batch => batch)
            .Where(m => m.Role == ChatRole.User)
            .SelectMany(m => m.Contents.OfType<TextContent>())
            .Select(c => c.Text)
            .ToList();

        userMessages.Should().ContainSingle().Which.Should().Be("What is the capital of Canada?");
    }

    [Fact]
    public async Task Agent_Sends_System_Instructions()
    {
        var stub = new StubChatClient("Canberra.");
        var agent = new ChatClientAgent(stub, instructions: Program.Instructions, name: "first-agent");

        await Program.Ask(agent, "What is the capital of Australia?");

        // MAF threads instructions through ChatOptions.Instructions, not as a System-role message.
        var optionsInstructions = string.Join(
            " ",
            stub.ReceivedOptions
                .Select(o => o?.Instructions ?? string.Empty));

        var systemMessages = string.Join(
            " ",
            stub.ReceivedMessages
                .SelectMany(batch => batch)
                .Where(m => m.Role == ChatRole.System)
                .SelectMany(m => m.Contents.OfType<TextContent>())
                .Select(c => c.Text));

        (optionsInstructions + " " + systemMessages).Should().Contain(Program.Instructions);
    }

    [Fact]
    public void Agent_Name_Is_Set()
    {
        var stub = new StubChatClient("noop");
        var agent = new ChatClientAgent(stub, instructions: Program.Instructions, name: "first-agent");
        agent.Name.Should().Be("first-agent");
    }

    // ─────────── Integration (hits real LLM) ───────────

    [Fact]
    [Trait("Category", "Integration")]
    public async Task Real_LLM_Returns_Paris_For_Capital_Of_France()
    {
        if (!LlmCredentialsPresent())
        {
            // Log and succeed; xunit has no first-class skip without a plugin.
            Console.WriteLine("[skip] no LLM credentials in .env");
            return;
        }

        var agent = Program.BuildAgent();
        var answer = await Program.Ask(agent, "What is the capital of France? Answer with the city name only.");
        answer.ToLowerInvariant().Should().Contain("paris");
    }

    // ─────────── Helpers ───────────

    private sealed class StubChatClient : IChatClient
    {
        private readonly Queue<string> _responses;
        public int CallCount { get; private set; }
        public List<IEnumerable<ChatMessage>> ReceivedMessages { get; } = new();
        public List<ChatOptions?> ReceivedOptions { get; } = new();

        public StubChatClient(params string[] canned) => _responses = new Queue<string>(canned);

        public Task<ChatResponse> GetResponseAsync(
            IEnumerable<ChatMessage> messages,
            ChatOptions? options = null,
            CancellationToken cancellationToken = default)
        {
            CallCount++;
            var materialised = messages.ToList();
            ReceivedMessages.Add(materialised);
            ReceivedOptions.Add(options);

            if (_responses.Count == 0)
            {
                throw new InvalidOperationException("StubChatClient has no more canned responses.");
            }

            var text = _responses.Dequeue();
            var reply = new ChatMessage(ChatRole.Assistant, text);
            return Task.FromResult(new ChatResponse(reply));
        }

        public IAsyncEnumerable<ChatResponseUpdate> GetStreamingResponseAsync(
            IEnumerable<ChatMessage> messages,
            ChatOptions? options = null,
            CancellationToken cancellationToken = default) =>
            throw new NotSupportedException("Streaming not used in Chapter 01 tests.");

        public object? GetService(Type serviceType, object? serviceKey = null) => null;

        public void Dispose() { }
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

// Tiny shim: xunit's [Fact] can't skip conditionally without a plugin. We
// use this skipper inside tests that need to bail when credentials are absent.
internal static class Skip
{
    public static void IfNot(bool condition, string reason)
    {
        if (!condition)
        {
            throw new SkipException(reason);
        }
    }
}

internal sealed class SkipException : Exception
{
    public SkipException(string reason) : base(reason) { }
}
