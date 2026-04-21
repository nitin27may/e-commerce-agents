// MAF v1 — Chapter 03: Streaming and Multi-turn (.NET)
//
// Stream tokens as they arrive; reuse one AgentSession across turns so the
// LLM sees the full conversation.

using System.ClientModel;
using Azure.AI.OpenAI;
using Microsoft.Agents.AI;
using OpenAI;
using OpenAI.Chat;

namespace MafV1.Ch03.Streaming;

public static class Program
{
    public const string Instructions =
        "You are a concise assistant. Keep answers to one short paragraph.";

    public static async Task Main(string[] args)
    {
        LoadDotEnv();
        var agent = BuildAgent();

        if (args.Length > 0)
        {
            await Chat(agent, args);
            return;
        }

        Console.WriteLine("Multi-turn chat (empty line to quit).");
        var thread = await agent.CreateSessionAsync();
        while (true)
        {
            Console.Write("\nQ: ");
            var q = Console.ReadLine()?.Trim();
            if (string.IsNullOrEmpty(q))
            {
                break;
            }
            Console.Write("A: ");
            await StreamAnswer(agent, q, thread);
        }
    }

    public static AIAgent BuildAgent()
    {
        var provider = Environment.GetEnvironmentVariable("LLM_PROVIDER")?.ToLowerInvariant() ?? "openai";
        ChatClient chatClient;

        if (provider == "azure")
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

        return chatClient.AsAIAgent(instructions: Instructions, name: "chat-agent");
    }

    public static async Task<List<string>> StreamAnswer(AIAgent agent, string question, AgentSession thread)
    {
        var chunks = new List<string>();
        await foreach (var update in agent.RunStreamingAsync(question, thread))
        {
            if (!string.IsNullOrEmpty(update.Text))
            {
                chunks.Add(update.Text);
                Console.Write(update.Text);
            }
        }
        Console.WriteLine();
        return chunks;
    }

    public static async Task<List<List<string>>> Chat(AIAgent agent, IReadOnlyList<string> questions)
    {
        var thread = await agent.CreateSessionAsync();
        var allChunks = new List<List<string>>();
        foreach (var q in questions)
        {
            Console.WriteLine($"\nQ: {q}");
            Console.Write("A: ");
            allChunks.Add(await StreamAnswer(agent, q, thread));
        }
        return allChunks;
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
