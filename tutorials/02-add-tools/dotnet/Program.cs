// MAF v1 — Chapter 02: Adding Tools (.NET)
//
// Same agent as Ch01 plus one canned weather tool. The LLM decides whether
// to call the tool based on the user's question.

using System.ClientModel;
using System.ComponentModel;
using Azure.AI.OpenAI;
using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;
using OpenAI;
using OpenAI.Chat;

namespace MafV1.Ch02.AddTools;

public static class Program
{
    public const string Instructions =
        "You are a helpful assistant. "
        + "When the user asks about the weather in a city, call the get_weather tool. "
        + "For other questions, answer directly in one short sentence.";

    public const string DefaultQuestion = "What's the weather in Paris?";

    public static async Task Main(string[] args)
    {
        LoadDotEnv();
        var question = args.Length > 0 ? args[0] : DefaultQuestion;
        var agent = BuildAgent();
        var answer = await Ask(agent, question);
        Console.WriteLine($"Q: {question}");
        Console.WriteLine($"A: {answer}");
    }

    public static AIAgent BuildAgent()
    {
        var provider = Environment.GetEnvironmentVariable("LLM_PROVIDER")?.ToLowerInvariant() ?? "openai";
        ChatClient chatClient;

        if (provider == "azure")
        {
            var endpoint = Required("AZURE_OPENAI_ENDPOINT");
            var deployment = Required("AZURE_OPENAI_DEPLOYMENT");
            var apiKey = Environment.GetEnvironmentVariable("AZURE_OPENAI_KEY")
                         ?? Environment.GetEnvironmentVariable("AZURE_OPENAI_API_KEY")
                         ?? throw new InvalidOperationException("Azure requires AZURE_OPENAI_KEY.");
            chatClient = new AzureOpenAIClient(new Uri(endpoint), new ApiKeyCredential(apiKey))
                .GetChatClient(deployment);
        }
        else
        {
            var openAiKey = Required("OPENAI_API_KEY");
            var model = Environment.GetEnvironmentVariable("LLM_MODEL") ?? "gpt-4.1";
            chatClient = new OpenAIClient(new ApiKeyCredential(openAiKey)).GetChatClient(model);
        }

        var tools = new AITool[] { AIFunctionFactory.Create(GetWeather) };

        return chatClient.AsAIAgent(
            instructions: Instructions,
            name: "weather-agent",
            tools: tools);
    }

    public static async Task<string> Ask(AIAgent agent, string question)
    {
        var response = await agent.RunAsync(question);
        return response.Text;
    }

    /// <summary>
    /// Canned-data weather lookup. The [Description] attribute drives the JSON
    /// schema the LLM sees when deciding whether to call this function.
    /// </summary>
    [Description("Look up the current weather for a city.")]
    public static string GetWeather(
        [Description("The city to look up, e.g. 'Paris'.")] string city)
    {
        var canned = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            ["Paris"] = "Sunny, 18°C, light breeze.",
            ["London"] = "Overcast, 12°C, light drizzle.",
            ["Canberra"] = "Partly cloudy, 21°C.",
            ["Tokyo"] = "Rain, 15°C.",
        };
        return canned.TryGetValue(city, out var forecast) ? forecast : $"No weather data for {city}.";
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
