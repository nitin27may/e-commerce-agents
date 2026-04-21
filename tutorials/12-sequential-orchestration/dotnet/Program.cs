// MAF v1 — Chapter 12: Sequential Orchestration (.NET)
//
// AgentWorkflowBuilder.BuildSequential(agents) chains AIAgent instances into
// a Pregel-style workflow where each agent sees the shared conversation so
// far and appends its turn. Runnable counterpart to the Python chapter:
// Writer -> Reviewer -> Finalizer.
//
// Run:
//   dotnet run                          # uses default topic
//   dotnet run -- "Why sleep matters"   # custom topic

using System.ClientModel;
using Azure.AI.OpenAI;
using Microsoft.Agents.AI;
using Microsoft.Agents.AI.Workflows;
using OpenAI;
using OpenAI.Chat;

namespace MafV1.Ch12.Sequential;

public static class Program
{
    public const string WriterInstructions =
        "You are a Writer. Draft a 2-sentence paragraph on the topic the user provides. Keep it short.";

    public const string ReviewerInstructions =
        "You are a Reviewer. Read the draft above and produce a single-sentence review "
        + "pointing out one strength and one weakness. Do not rewrite the draft.";

    public const string FinalizerInstructions =
        "You are a Finalizer. Produce a one-sentence final version of the paragraph that "
        + "addresses the reviewer's feedback. Output ONLY the final sentence — no preamble.";

    public static async Task<int> Main(string[] args)
    {
        LoadDotEnv();
        var topic = args.Length > 0 ? args[0] : "quantum computing basics";

        Console.WriteLine($"Topic: {topic}");
        Console.WriteLine();

        var workflow = BuildWorkflow();

        // RunStreamingAsync yields lifecycle events as the workflow progresses.
        // We match on AgentResponseEvent to print each agent's turn in order.
        await using var run = await InProcessExecution.RunStreamingAsync(workflow, topic);
        await foreach (var evt in run.WatchStreamAsync())
        {
            if (evt is AgentResponseEvent r)
            {
                Console.WriteLine($"{r.ExecutorId,-9}: {r.Response.Text}");
                Console.WriteLine();
            }
        }

        return 0;
    }

    /// <summary>
    /// Builds the Writer -> Reviewer -> Finalizer pipeline using the convenience
    /// builder. BuildSequential wires input/output adapters and the shared
    /// conversation forwarding — no manual AgentExecutor scaffolding required.
    /// </summary>
    public static Workflow BuildWorkflow()
    {
        var chatClient = BuildChatClient();

        AIAgent writer = chatClient.AsAIAgent(instructions: WriterInstructions, name: "writer");
        AIAgent reviewer = chatClient.AsAIAgent(instructions: ReviewerInstructions, name: "reviewer");
        AIAgent finalizer = chatClient.AsAIAgent(instructions: FinalizerInstructions, name: "finalizer");

        return AgentWorkflowBuilder.BuildSequential(new[] { writer, reviewer, finalizer });
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
