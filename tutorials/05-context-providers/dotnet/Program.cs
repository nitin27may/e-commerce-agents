// MAF v1 — Chapter 05: Context Providers (.NET)
//
// Inject per-request user context into the agent via an AIContextProvider.
// The provider's InvokingAsync runs before every LLM call and returns an
// AIContext whose Instructions get appended to the agent's system prompt.

using System.ClientModel;
using Azure.AI.OpenAI;
using Microsoft.Agents.AI;
using OpenAI;
using OpenAI.Chat;

namespace MafV1.Ch05.ContextProviders;

public static class Program
{
    public const string Instructions =
        "You are a personal shopping assistant. "
        + "Greet the user by name if you know it. Keep answers short.";

    public static async Task Main(string[] args)
    {
        LoadDotEnv();

        var email = args.Length > 0 ? args[0] : "alice@example.com";
        var name = args.Length > 1 ? args[1] : "Alice";
        var tier = args.Length > 2 ? args[2] : "gold";

        var agent = BuildAgent(new UserProfileProvider(email, name, tier));
        var response = await agent.RunAsync("Greet me and tell me what tier I'm on.");
        Console.WriteLine($"A: {response.Text}");
    }

    public static AIAgent BuildAgent(AIContextProvider provider)
    {
        var provider_env = Environment.GetEnvironmentVariable("LLM_PROVIDER")?.ToLowerInvariant() ?? "openai";
        ChatClient chatClient;

        if (provider_env == "azure")
        {
            chatClient = new AzureOpenAIClient(
                new Uri(Required("AZURE_OPENAI_ENDPOINT")),
                new ApiKeyCredential(
                    Environment.GetEnvironmentVariable("AZURE_OPENAI_KEY")
                    ?? Required("AZURE_OPENAI_API_KEY")))
                .GetChatClient(Required("AZURE_OPENAI_DEPLOYMENT"));
        }
        else
        {
            chatClient = new OpenAIClient(new ApiKeyCredential(Required("OPENAI_API_KEY")))
                .GetChatClient(Environment.GetEnvironmentVariable("LLM_MODEL") ?? "gpt-4.1");
        }

        return chatClient.AsAIAgent(new ChatClientAgentOptions
        {
            Name = "personalized-agent",
            ChatOptions = new Microsoft.Extensions.AI.ChatOptions { Instructions = Instructions },
            AIContextProviders = new[] { provider },
        });
    }

    /// <summary>
    /// AIContextProvider that appends the current user's details to the
    /// per-run Instructions. InvokingAsync fires before each LLM call.
    /// </summary>
    public sealed class UserProfileProvider : AIContextProvider
    {
        public string Email { get; }
        public string Name { get; }
        public string LoyaltyTier { get; }

        public UserProfileProvider(string email, string name, string loyaltyTier = "silver")
        {
            Email = email;
            Name = name;
            LoyaltyTier = loyaltyTier;
        }

        // MAF invokes ProvideAIContextAsync before each agent run. Return an
        // AIContext with Instructions that get appended to the system prompt.
        protected override ValueTask<AIContext> ProvideAIContextAsync(
            InvokingContext context,
            CancellationToken cancellationToken = default)
        {
            return ValueTask.FromResult(new AIContext
            {
                Instructions = $"Current user: {Name} ({Email}). Loyalty tier: {LoyaltyTier}.",
            });
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
