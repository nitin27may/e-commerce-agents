// MAF v1 — Chapter 12: Sequential Orchestration (.NET)
//
// AgentWorkflowBuilder.BuildSequential(agents) chains AIAgent instances into
// a Pregel-style workflow where each agent sees the shared conversation so
// far and appends its turn. Runnable counterpart to the Python chapter:
// Writer -> Reviewer -> Finalizer.
//
// Two things about this builder are easy to get wrong, and getting either
// wrong produces a run that exits 0 having done nothing at all:
//
//   1. The workflow's input type is List<ChatMessage>, not string. Handing it
//      a bare topic string starts the run but never reaches an agent.
//   2. The wrapped agents are lazy. AgentExecutor caches inbound messages and
//      only calls the LLM once a TurnToken arrives, so the run must send one
//      with run.TrySendMessageAsync(new TurnToken(emitEvents: true)).
//
// Both are the same trap Chapter 11 flagged for the manual builder; they do
// not go away just because BuildSequential hides the adapters.
//
// A third: BuildSequential emits AgentResponseUpdateEvent, never
// AgentResponseEvent. Matching on the latter compiles, runs, and silently
// prints nothing. The terminal WorkflowOutputEvent carries the whole
// conversation with ChatMessage.AuthorName set per agent, which is the
// reliable place to read each turn from — that is what RunAsync returns.
//
// Run:
//   dotnet run                          # uses default topic
//   dotnet run -- "Why sleep matters"   # custom topic

using System.ClientModel;
using Azure.AI.OpenAI;
using Microsoft.Agents.AI;
using Microsoft.Agents.AI.Workflows;
using Microsoft.Extensions.AI;
using OpenAI;
using OpenAI.Chat;

// Both Microsoft.Extensions.AI and OpenAI.Chat define a ChatMessage; the MAF
// workflow surface wants the former, BuildChatClient returns the latter's client.
using ChatMessage = Microsoft.Extensions.AI.ChatMessage;

namespace MafV1.Ch12.Sequential;

/// <summary>One agent's contribution to the pipeline, in the order it ran.</summary>
public sealed record Turn(string ExecutorId, string Text);

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

        IReadOnlyList<Turn> turns = await RunAsync(BuildChatClient().AsIChatClient(), topic);

        foreach (Turn turn in turns)
        {
            Console.WriteLine($"{turn.ExecutorId,-9}: {turn.Text}");
            Console.WriteLine();
        }

        return 0;
    }

    /// <summary>
    /// Runs the Writer -> Reviewer -> Finalizer pipeline and returns each
    /// agent's turn in order.
    /// </summary>
    /// <remarks>
    /// Taking an <see cref="IChatClient"/> rather than reaching for the
    /// environment is what makes this chapter testable: the test project
    /// passes a scripted client and asserts the ordering and the shared
    /// conversation without a key, a network call, or a fixture.
    /// </remarks>
    public static async Task<IReadOnlyList<Turn>> RunAsync(IChatClient chatClient, string topic)
    {
        Workflow workflow = BuildWorkflow(chatClient);

        // BuildSequential's adapters expect a conversation, not a topic.
        var messages = new List<ChatMessage> { new(ChatRole.User, topic) };

        await using StreamingRun run = await InProcessExecution.RunStreamingAsync(workflow, messages);

        // Without this, every wrapped agent sits on its cached input forever
        // and the stream completes with no agent output at all.
        await run.TrySendMessageAsync(new TurnToken(emitEvents: true));

        List<ChatMessage>? conversation = null;
        await foreach (WorkflowEvent evt in run.WatchStreamAsync())
        {
            if (evt is WorkflowOutputEvent output && output.Data is List<ChatMessage> final)
            {
                conversation = final;
            }
        }

        if (conversation is null)
        {
            return Array.Empty<Turn>();
        }

        // The first message is the topic we put in; every assistant message
        // after it is one agent's turn, tagged with the agent's name.
        return conversation
            .Where(m => m.Role == ChatRole.Assistant)
            .Select(m => new Turn(m.AuthorName ?? "(unnamed)", m.Text.Trim()))
            .ToList();
    }

    /// <summary>
    /// Builds the Writer -> Reviewer -> Finalizer pipeline using the convenience
    /// builder. BuildSequential wires input/output adapters and the shared
    /// conversation forwarding — no manual AgentExecutor scaffolding required.
    /// </summary>
    public static Workflow BuildWorkflow() => BuildWorkflow(BuildChatClient().AsIChatClient());

    /// <summary>The same pipeline over any <see cref="IChatClient"/>.</summary>
    public static Workflow BuildWorkflow(IChatClient chatClient)
    {
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
