// MAF v1 — Chapter 14: Handoff Orchestration (.NET)
//
// A Triage agent reads the user's question and hands off to a Math or History
// specialist via a synthesised handoff tool call. Specialists can hand back to
// Triage for follow-ups. Demonstrates the convenience builder
// AgentWorkflowBuilder.CreateHandoffBuilderWith(...).WithHandoffs(...)
// and the interactive request/response loop the mesh topology requires.
//
// Run:
//   cd tutorials/14-handoff-orchestration/dotnet
//   dotnet run -- "What is 37 * 42?"
//   dotnet run -- "When did World War 2 end?"
//
// Requires OPENAI_API_KEY (or Azure OpenAI env vars) in repo-root .env.

using System.ClientModel;
using Azure.AI.OpenAI;
using Microsoft.Agents.AI;
using Microsoft.Agents.AI.Workflows;
using Microsoft.Extensions.AI;
using OpenAI;
using OpenAI.Chat;

using ChatMessage = Microsoft.Extensions.AI.ChatMessage;

namespace MafV1.Ch14.Handoff;

public static class Program
{
    private const string TriageInstructions =
        "You are a Triage agent. Read the user's question and hand off to the right "
        + "specialist via the provided handoff tool: math questions go to math_tutor, "
        + "historical questions go to history_tutor. ALWAYS handoff; do not answer directly.";

    private const string MathInstructions =
        "You are a Math expert. Answer arithmetic and math questions directly in ONE "
        + "short sentence containing the numerical answer. Do not hand off back unless "
        + "the question is clearly not about math.";

    private const string HistoryInstructions =
        "You are a History expert. Answer historical questions in ONE short sentence "
        + "with the specific date or year. Do not hand off back unless the question is "
        + "clearly not about history.";

    public static async Task<int> Main(string[] args)
    {
        LoadDotEnv();

        string question = args.Length > 0
            ? args[0]
            : "What is 37 * 42?";

        Console.WriteLine($"Q: {question}");
        Console.WriteLine();

        ChatClient chatClient = BuildChatClient();

        // AsAIAgent(instructions, name, description). The `description` shows up
        // as the default handoff reason that the builder stamps into each
        // synthesised `handoff_to_<name>` tool's JSON schema.
        AIAgent triage = chatClient.AsAIAgent(
            instructions: TriageInstructions,
            name: "triage_agent",
            description: "Routes questions to the appropriate specialist.");
        AIAgent mathTutor = chatClient.AsAIAgent(
            instructions: MathInstructions,
            name: "math_tutor",
            description: "Specialist agent for math and arithmetic questions.");
        AIAgent historyTutor = chatClient.AsAIAgent(
            instructions: HistoryInstructions,
            name: "history_tutor",
            description: "Specialist agent for historical questions, dates, and events.");

        // Build the mesh. Every source needs an explicit WithHandoffs edge list;
        // agents without one cannot invoke any handoff tool.
        //   triage -> { math_tutor, history_tutor }
        //   math_tutor -> { triage }
        //   history_tutor -> { triage }
        Workflow workflow = AgentWorkflowBuilder.CreateHandoffBuilderWith(triage)
            .WithHandoffs(triage, new[] { mathTutor, historyTutor })
            .WithHandoffs(new[] { mathTutor, historyTutor }, triage)
            .Build();

        var messages = new List<ChatMessage> { new(ChatRole.User, question) };
        var routing = new List<string>();
        string? lastExecutorId = null;
        List<ChatMessage>? newMessages = null;

        await using StreamingRun run = await InProcessExecution.RunStreamingAsync(workflow, messages);
        await run.TrySendMessageAsync(new TurnToken(emitEvents: true));

        await foreach (WorkflowEvent evt in run.WatchStreamAsync())
        {
            switch (evt)
            {
                case AgentResponseUpdateEvent update:
                    // Streaming deltas — one event per token. Print the agent
                    // label the first time each executor speaks so the routing
                    // is visible on the console.
                    if (update.ExecutorId != lastExecutorId)
                    {
                        lastExecutorId = update.ExecutorId;
                        routing.Add(update.ExecutorId ?? "agent");
                        Console.WriteLine();
                        Console.WriteLine($"[{update.ExecutorId}]");
                    }
                    Console.Write(update.Update.Text);
                    break;

                case WorkflowOutputEvent output when output.Data is List<ChatMessage> list:
                    // The run completes either when an agent declines to hand
                    // off (and no more input is expected) or when the workflow
                    // pauses for user input. Either way, the accumulated
                    // conversation arrives here.
                    newMessages = list;
                    break;
            }
        }

        Console.WriteLine();
        Console.WriteLine();
        Console.WriteLine($"Routing: {string.Join(" -> ", routing)}");

        if (newMessages is { Count: > 0 })
        {
            ChatMessage last = newMessages[^1];
            Console.WriteLine($"Final : {last.Text.Trim()}");
        }

        return 0;
    }

    private static ChatClient BuildChatClient()
    {
        string provider = Environment.GetEnvironmentVariable("LLM_PROVIDER")?.ToLowerInvariant() ?? "openai";

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
        if (dir is null)
        {
            return;
        }

        foreach (string raw in File.ReadAllLines(Path.Combine(dir.FullName, ".env")))
        {
            string line = raw.Trim();
            if (line.Length == 0 || line.StartsWith('#'))
            {
                continue;
            }
            int eq = line.IndexOf('=');
            if (eq < 0)
            {
                continue;
            }
            string key = line[..eq].Trim();
            string value = line[(eq + 1)..].Trim().Trim('"').Trim('\'');
            if (Environment.GetEnvironmentVariable(key) is null)
            {
                Environment.SetEnvironmentVariable(key, value);
            }
        }
    }
}
