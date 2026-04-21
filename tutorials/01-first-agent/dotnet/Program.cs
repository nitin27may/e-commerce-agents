// MAF v1 — Chapter 01: Your First Agent (.NET)
//
// Minimum viable code to stand up a Microsoft Agent Framework agent against
// OpenAI or Azure OpenAI and ask it one question.
//
// Run from the repo root:
//   source .env (or set vars manually)
//   cd tutorials/01-first-agent/dotnet
//   dotnet run
//
// Override the question:
//   dotnet run -- "Why is the sky blue?"

using System.ClientModel;
using Azure.AI.OpenAI;
using Microsoft.Agents.AI;
using OpenAI;
using OpenAI.Chat;

namespace MafV1.Ch01.FirstAgent;

public static class Program
{
    public const string Instructions =
        "You are a concise geography assistant. Keep answers to one short sentence.";
    public const string DefaultQuestion = "What is the capital of France?";

    public static async Task Main(string[] args)
    {
        LoadDotEnv();

        var question = args.Length > 0 ? args[0] : DefaultQuestion;
        var agent = BuildAgent();

        var answer = await Ask(agent, question);
        Console.WriteLine($"Q: {question}");
        Console.WriteLine($"A: {answer}");
    }

    /// <summary>
    /// Builds the agent from the current environment. Tests inject their own
    /// agent so this factory is not called during unit tests.
    /// </summary>
    public static AIAgent BuildAgent()
    {
        var provider = Environment.GetEnvironmentVariable("LLM_PROVIDER")?.ToLowerInvariant() ?? "openai";

        if (provider == "azure")
        {
            var endpoint = Required("AZURE_OPENAI_ENDPOINT");
            var deployment = Required("AZURE_OPENAI_DEPLOYMENT");
            var apiKey = Environment.GetEnvironmentVariable("AZURE_OPENAI_KEY")
                         ?? Environment.GetEnvironmentVariable("AZURE_OPENAI_API_KEY")
                         ?? throw new InvalidOperationException(
                             "Azure requires AZURE_OPENAI_KEY (or AZURE_OPENAI_API_KEY).");

            var azureClient = new AzureOpenAIClient(new Uri(endpoint), new ApiKeyCredential(apiKey));
            var chatClient = azureClient.GetChatClient(deployment);
            return chatClient.AsAIAgent(instructions: Instructions, name: "first-agent");
        }

        var openAiKey = Required("OPENAI_API_KEY");
        var model = Environment.GetEnvironmentVariable("LLM_MODEL") ?? "gpt-4.1";
        var openAi = new OpenAIClient(new ApiKeyCredential(openAiKey));
        return openAi.GetChatClient(model)
            .AsAIAgent(instructions: Instructions, name: "first-agent");
    }

    /// <summary>
    /// Invokes the agent and returns the plain-text answer.
    /// </summary>
    public static async Task<string> Ask(AIAgent agent, string question)
    {
        var response = await agent.RunAsync(question);
        return response.Text;
    }

    private static string Required(string name) =>
        Environment.GetEnvironmentVariable(name)
            ?? throw new InvalidOperationException($"{name} must be set (see repo-root .env).");

    /// <summary>
    /// Walks up from the current directory to find the repo-root .env and
    /// loads key=value lines into process env. Skips comments and blank lines.
    /// Does not overwrite variables already set by the caller.
    /// </summary>
    private static void LoadDotEnv()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !File.Exists(Path.Combine(dir.FullName, ".env")))
        {
            dir = dir.Parent;
        }

        if (dir is null)
        {
            return;
        }

        foreach (var raw in File.ReadAllLines(Path.Combine(dir.FullName, ".env")))
        {
            var line = raw.Trim();
            if (line.Length == 0 || line.StartsWith('#'))
            {
                continue;
            }

            var eq = line.IndexOf('=');
            if (eq < 0)
            {
                continue;
            }

            var key = line[..eq].Trim();
            var value = line[(eq + 1)..].Trim().Trim('"').Trim('\'');
            if (Environment.GetEnvironmentVariable(key) is null)
            {
                Environment.SetEnvironmentVariable(key, value);
            }
        }
    }
}
