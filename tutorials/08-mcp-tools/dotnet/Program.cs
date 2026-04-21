// MAF v1 — Chapter 08: MCP Tools (.NET)
//
// Connect an agent to the SAME local MCP server used by the Python chapter
// (a tiny Python weather server over stdio). ModelContextProtocol spawns
// the subprocess and MAF treats each MCP tool like a native AIFunction.

using System.ClientModel;
using Azure.AI.OpenAI;
using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;
using ModelContextProtocol.Client;
using OpenAI;
using OpenAI.Chat;

namespace MafV1.Ch08.McpTools;

public static class Program
{
    public const string Instructions =
        "You are a helpful assistant. "
        + "When the user asks about weather in a city, call the get_weather tool. "
        + "Keep answers to one short sentence.";

    private static string ServerScript => Path.Combine(
        // Walk from the running dotnet binary up to the tutorials/08 folder,
        // then over to the Python server file.
        FindRepoRoot(), "tutorials", "08-mcp-tools", "python", "weather_mcp_server.py");

    public static async Task Main(string[] args)
    {
        LoadDotEnv();
        var question = args.Length > 0 ? args[0] : "What's the weather in Paris?";
        var answer = await Run(question);
        Console.WriteLine($"Q: {question}");
        Console.WriteLine($"A: {answer}");
    }

    public static async Task<string> Run(string question)
    {
        await using var mcpClient = await BuildMcpClientAsync();
        var tools = (await mcpClient.ListToolsAsync()).Select(t => (AITool)t).ToArray();

        var chatClient = BuildChatClient();
        var agent = chatClient.AsAIAgent(
            instructions: Instructions,
            name: "mcp-agent",
            tools: tools);

        var response = await agent.RunAsync(question);
        return response.Text;
    }

    public static async Task<McpClient> BuildMcpClientAsync()
    {
        var pythonBin = Environment.GetEnvironmentVariable("PYTHON_BIN")
                        ?? Path.Combine(FindRepoRoot(), "agents", ".venv", "bin", "python");

        var transport = new StdioClientTransport(new StdioClientTransportOptions
        {
            Name = "weather-mcp",
            Command = pythonBin,
            Arguments = new[] { ServerScript },
        });

        return await McpClient.CreateAsync(transport);
    }

    public static ChatClient BuildChatClient()
    {
        var provider = Environment.GetEnvironmentVariable("LLM_PROVIDER")?.ToLowerInvariant() ?? "openai";
        if (provider == "azure")
        {
            return new AzureOpenAIClient(
                new Uri(Required("AZURE_OPENAI_ENDPOINT")),
                new ApiKeyCredential(
                    Environment.GetEnvironmentVariable("AZURE_OPENAI_KEY")
                    ?? Required("AZURE_OPENAI_API_KEY")))
                .GetChatClient(Required("AZURE_OPENAI_DEPLOYMENT"));
        }

        return new OpenAIClient(new ApiKeyCredential(Required("OPENAI_API_KEY")))
            .GetChatClient(Environment.GetEnvironmentVariable("LLM_MODEL") ?? "gpt-4.1");
    }

    private static string Required(string name) =>
        Environment.GetEnvironmentVariable(name)
            ?? throw new InvalidOperationException($"{name} must be set (see repo-root .env).");

    private static string FindRepoRoot()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !File.Exists(Path.Combine(dir.FullName, ".env.example")))
        {
            dir = dir.Parent;
        }
        return dir?.FullName
               ?? throw new DirectoryNotFoundException("Could not locate repo root.");
    }

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
