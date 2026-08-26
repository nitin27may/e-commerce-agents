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

/// <summary>What one trip through the handoff mesh produced.</summary>
/// <param name="Routing">Executor ids in the order they first spoke.</param>
/// <param name="Final">The last non-empty assistant message.</param>
/// <param name="Conversation">Everything the run accumulated.</param>
public sealed record HandoffResult(
    IReadOnlyList<string> Routing,
    string Final,
    IReadOnlyList<ChatMessage> Conversation);

public static class Program
{
    internal const string TriageInstructions =
        "You are a Triage agent. Read the user's question and hand off to the right "
        + "specialist via the provided handoff tool: math questions go to math_tutor, "
        + "historical questions go to history_tutor. ALWAYS handoff; do not answer directly.";

    internal const string MathInstructions =
        "You are a Math expert. Answer arithmetic and math questions directly in ONE "
        + "short sentence containing the numerical answer. Do not hand off back unless "
        + "the question is clearly not about math.";

    internal const string HistoryInstructions =
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

        HandoffResult result = await RunAsync(BuildChatClient().AsIChatClient(), question);

        foreach (ChatMessage message in result.Conversation)
        {
            if (message.Role == ChatRole.Assistant && !string.IsNullOrWhiteSpace(message.Text))
            {
                Console.WriteLine($"[{message.AuthorName ?? "agent"}] {message.Text.Trim()}");
            }
        }

        Console.WriteLine();
        Console.WriteLine($"Routing: {string.Join(" -> ", result.Routing)}");
        Console.WriteLine($"Final  : {result.Final}");

        return 0;
    }

    /// <summary>
    /// Builds the triage/math/history mesh over any <see cref="IChatClient"/>.
    /// </summary>
    /// <remarks>
    /// Every source needs an explicit WithHandoffs edge list; an agent without
    /// one is handed no handoff tools at all and can only answer directly.
    /// That failure is silent — the agent just stops routing — which is why
    /// the test project asserts on the tool names each agent is offered.
    ///   triage -> { math_tutor, history_tutor }
    ///   math_tutor -> { triage }
    ///   history_tutor -> { triage }
    /// </remarks>
    public static Workflow BuildWorkflow(IChatClient chatClient)
    {
        // AsAIAgent(instructions, name, description). `description` is load-
        // bearing, not documentation. Microsoft.Agents.AI.Workflows 1.1.0 names
        // the synthesised handoff tools POSITIONALLY — handoff_to_1,
        // handoff_to_2 — so the agent's own name never reaches the model. The
        // description is the only thing distinguishing one handoff target from
        // another in the tool schema. Omit it and the model is choosing between
        // two identically-nameless tools; it will still pick one, which is why
        // this misroutes rather than erroring.
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

        return AgentWorkflowBuilder.CreateHandoffBuilderWith(triage)
            .WithHandoffs(triage, new[] { mathTutor, historyTutor })
            .WithHandoffs(new[] { mathTutor, historyTutor }, triage)
            .Build();
    }

    /// <summary>
    /// Runs one question through the mesh and reports where it went.
    /// </summary>
    public static async Task<HandoffResult> RunAsync(IChatClient chatClient, string question)
    {
        Workflow workflow = BuildWorkflow(chatClient);

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
                    // Streaming deltas. Record the executor the first time it
                    // speaks — that sequence IS the routing decision, and it is
                    // the only place the handoff is observable.
                    if (update.ExecutorId != lastExecutorId)
                    {
                        lastExecutorId = update.ExecutorId;
                        routing.Add(update.ExecutorId ?? "agent");
                    }
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

        List<ChatMessage> conversation = newMessages ?? new List<ChatMessage>();
        string final = conversation
            .LastOrDefault(m => m.Role == ChatRole.Assistant && !string.IsNullOrWhiteSpace(m.Text))
            ?.Text.Trim() ?? string.Empty;

        return new HandoffResult(routing, final, conversation);
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
