// MAF v1 — Chapter 04: Sessions and Memory (.NET)
//
// Persist an AgentSession to disk between runs so conversation state
// survives process restarts.

using System.ClientModel;
using System.Text.Json;
using Azure.AI.OpenAI;
using Microsoft.Agents.AI;
using OpenAI;
using OpenAI.Chat;

namespace MafV1.Ch04.Sessions;

public static class Program
{
    public const string Instructions = "You are a helpful assistant. Keep answers short.";

    public static readonly string SessionFile =
        Path.Combine(AppContext.BaseDirectory, "session.json");

    public static async Task Main(string[] args)
    {
        LoadDotEnv();
        var mode = args.Length > 0 ? args[0] : "save";
        var question = args.Length > 1 ? args[1] : "Hello!";

        if (mode == "reset" && File.Exists(SessionFile))
        {
            File.Delete(SessionFile);
            Console.WriteLine("Session cleared.");
            return;
        }

        var agent = BuildAgent();
        var (answer, path) = await AskAndSave(agent, question, SessionFile);
        Console.WriteLine($"Q: {question}");
        Console.WriteLine($"A: {answer}");
        Console.WriteLine($"(session persisted to {Path.GetFileName(path)})");
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

        return chatClient.AsAIAgent(instructions: Instructions, name: "stateful-agent");
    }

    /// <summary>
    /// Loads the session from disk if present, runs one turn, and writes it back.
    /// Returns the model's answer and the final on-disk path.
    /// </summary>
    public static async Task<(string Answer, string Path)> AskAndSave(
        AIAgent agent, string question, string sessionPath)
    {
        var session = await LoadOrNew(agent, sessionPath);
        var response = await agent.RunAsync(question, session);
        await Save(agent, session, sessionPath);
        return (response.Text, sessionPath);
    }

    public static async Task<AgentSession> LoadOrNew(AIAgent agent, string path)
    {
        if (!File.Exists(path))
        {
            return await agent.CreateSessionAsync();
        }

        using var stream = File.OpenRead(path);
        using var doc = await JsonDocument.ParseAsync(stream);
        return await agent.DeserializeSessionAsync(doc.RootElement);
    }

    public static async Task Save(AIAgent agent, AgentSession session, string path)
    {
        // agent.SerializeSessionAsync turns the session (including the
        // conversation state the agent maintains) into a JsonElement; we
        // persist it as pretty-printed JSON.
        var element = await agent.SerializeSessionAsync(session);
        var json = JsonSerializer.Serialize(element, new JsonSerializerOptions { WriteIndented = true });
        await File.WriteAllTextAsync(path, json);
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
