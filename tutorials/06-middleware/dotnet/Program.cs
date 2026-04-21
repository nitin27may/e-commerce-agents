// MAF v1 — Chapter 06: Middleware (.NET)
//
// Two middleware kinds demonstrated:
// - Chat middleware (IChatClient pipeline) redacts credit-card-shaped
//   strings in user messages before the model sees them.
// - Function-invocation "middleware" baked into the tool function itself —
//   a guard that short-circuits a forbidden city with a canned refusal.
//
// (Agent-run middleware via AIAgentBuilder.Use(...) works the same way as
// the chat-client pipeline: wrap, observe, optionally mutate. We keep this
// chapter's example tight; the capstone's shared agent factory wires a full
// agent-run layer that logs + spans every invocation.)

using System.ClientModel;
using System.ComponentModel;
using System.Text.RegularExpressions;
using Azure.AI.OpenAI;
using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;
using OpenAI;
using OpenAIChatClient = OpenAI.Chat.ChatClient;
using ChatMessage = Microsoft.Extensions.AI.ChatMessage;
using OpenAI.Chat;

namespace MafV1.Ch06.Middleware;

public static class Program
{
    public const string Instructions =
        "You are a helpful assistant. "
        + "When the user asks about weather in a city, call get_weather. "
        + "Keep answers to one short sentence.";

    private static readonly Regex CardPattern =
        new(@"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b", RegexOptions.Compiled);

    public static async Task Main(string[] args)
    {
        LoadDotEnv();
        var question = args.Length > 0 ? args[0] : "What's the weather in Paris?";
        var stats = new Stats();
        var agent = BuildAgent(stats);
        var response = await agent.RunAsync(question);
        Console.WriteLine($"Q: {question}");
        Console.WriteLine($"A: {response.Text}");
        Console.WriteLine();
        Console.WriteLine($"tool invocations: {string.Join(", ", stats.ToolInvocations)}");
        Console.WriteLine($"tool blocked:     {string.Join(", ", stats.BlockedTools)}");
        Console.WriteLine($"pii redactions:   {stats.PiiRedactions}");
    }

    /// <summary>Shared container the middleware writes into so tests can assert what fired.</summary>
    public sealed class Stats
    {
        public List<string> ToolInvocations { get; } = new();
        public List<string> BlockedTools { get; } = new();
        public int PiiRedactions { get; set; }
    }

    public static AIAgent BuildAgent(Stats stats)
    {
        var provider = Environment.GetEnvironmentVariable("LLM_PROVIDER")?.ToLowerInvariant() ?? "openai";
        OpenAIChatClient rawChat;

        if (provider == "azure")
        {
            rawChat = new AzureOpenAIClient(
                new Uri(Required("AZURE_OPENAI_ENDPOINT")),
                new ApiKeyCredential(
                    Environment.GetEnvironmentVariable("AZURE_OPENAI_KEY")
                    ?? Required("AZURE_OPENAI_API_KEY")))
                .GetChatClient(Required("AZURE_OPENAI_DEPLOYMENT"));
        }
        else
        {
            rawChat = new OpenAIClient(new ApiKeyCredential(Required("OPENAI_API_KEY")))
                .GetChatClient(Environment.GetEnvironmentVariable("LLM_MODEL") ?? "gpt-4.1");
        }

        // Chat middleware — wrap the IChatClient pipeline with a delegate that
        // mutates outbound messages before they reach the provider.
        IChatClient pipeline = rawChat.AsIChatClient()
            .AsBuilder()
            .Use(new PiiRedactingChatClient.Factory(stats, CardPattern))
            .Build();

        var weather = AIFunctionFactory.Create(
            (
                [Description("The city to look up, e.g. 'Paris'.")] string city) =>
            {
                stats.ToolInvocations.Add(city);
                if (string.Equals(city, "Atlantis", StringComparison.OrdinalIgnoreCase))
                {
                    stats.BlockedTools.Add(city);
                    return "Refused: that city isn't supported.";
                }
                var canned = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
                {
                    ["Paris"] = "Sunny, 18°C.",
                    ["London"] = "Overcast, 12°C.",
                    ["Tokyo"] = "Rain, 15°C.",
                };
                return canned.TryGetValue(city, out var forecast) ? forecast : $"No weather data for {city}.";
            }, name: "get_weather", description: "Look up the current weather for a city.");

        return new ChatClientAgent(
            pipeline,
            new ChatClientAgentOptions
            {
                Name = "middleware-agent",
                ChatOptions = new ChatOptions
                {
                    Instructions = Instructions,
                    Tools = new[] { (AITool)weather },
                },
            });
    }

    /// <summary>PII-redacting chat client middleware as a delegating IChatClient.</summary>
    private sealed class PiiRedactingChatClient : DelegatingChatClient
    {
        private readonly Stats _stats;
        private readonly Regex _pattern;

        public PiiRedactingChatClient(IChatClient inner, Stats stats, Regex pattern)
            : base(inner)
        {
            _stats = stats;
            _pattern = pattern;
        }

        public override Task<ChatResponse> GetResponseAsync(
            IEnumerable<ChatMessage> messages,
            ChatOptions? options = null,
            CancellationToken cancellationToken = default)
        {
            Redact(messages);
            return base.GetResponseAsync(messages, options, cancellationToken);
        }

        public override IAsyncEnumerable<ChatResponseUpdate> GetStreamingResponseAsync(
            IEnumerable<ChatMessage> messages,
            ChatOptions? options = null,
            CancellationToken cancellationToken = default)
        {
            Redact(messages);
            return base.GetStreamingResponseAsync(messages, options, cancellationToken);
        }

        private void Redact(IEnumerable<ChatMessage> messages)
        {
            foreach (var message in messages)
            {
                for (var i = 0; i < message.Contents.Count; i++)
                {
                    if (message.Contents[i] is TextContent tc)
                    {
                        var count = 0;
                        var redacted = _pattern.Replace(tc.Text, _ =>
                        {
                            count++;
                            return "[REDACTED-CARD]";
                        });
                        if (count > 0)
                        {
                            _stats.PiiRedactions += count;
                            message.Contents[i] = new TextContent(redacted);
                        }
                    }
                }
            }
        }

        public sealed class Factory
        {
            private readonly Stats _stats;
            private readonly Regex _pattern;

            public Factory(Stats stats, Regex pattern)
            {
                _stats = stats;
                _pattern = pattern;
            }

            public static implicit operator Func<IChatClient, IChatClient>(Factory f) =>
                inner => new PiiRedactingChatClient(inner, f._stats, f._pattern);
        }
    }

    private static string Required(string name) =>
        Environment.GetEnvironmentVariable(name)
            ?? throw new InvalidOperationException($"{name} must be set (see repo-root .env).");

    private static void LoadDotEnv()
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
