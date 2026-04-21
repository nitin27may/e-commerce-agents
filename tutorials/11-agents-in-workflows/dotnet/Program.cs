// MAF v1 — Chapter 11: Agents in Workflows (.NET)
//
// Shows LLM reasoning as a step inside a deterministic workflow graph.
// Two real translation agents are chained English -> French -> Spanish.
//
// This file teaches TWO patterns side by side:
//
//   1. Convenience  -- AgentWorkflowBuilder.BuildSequential(agents)
//                      Wraps the agents as executors and handles the adapter
//                      plumbing for you. One line. Use this for pure
//                      agent-to-agent pipelines.
//
//   2. Manual       -- Custom [MessageHandler] executors that call
//                      agent.RunAsync(...) themselves. These are the
//                      "adapter executors" you need when the workflow
//                      mixes agents with raw data executors, uses custom
//                      session management, or wants to emit domain events
//                      around each LLM turn.
//
// Run:
//   source .env                              # load OPENAI_API_KEY / Azure creds
//   cd tutorials/11-agents-in-workflows/dotnet
//   dotnet run                               # defaults to --sequential
//   dotnet run -- --manual                   # custom executor pattern
//   dotnet run -- --sequential "Good night"  # override the English input

using System.ClientModel;
using Azure.AI.OpenAI;
using Microsoft.Agents.AI;
using Microsoft.Agents.AI.Workflows;
using Microsoft.Extensions.AI;
using OpenAI;

namespace MafV1.Ch11.AgentsInWorkflows;

public static class Program
{
    public const string DefaultInput = "Hello, how are you?";

    public static async Task<int> Main(string[] args)
    {
        LoadDotEnv();

        var (mode, input) = ParseArgs(args);
        var chatClient = BuildChatClient();

        Console.WriteLine($"mode:   {mode}");
        Console.WriteLine($"input:  {Quote(input)}");
        Console.WriteLine();

        var result = mode == Mode.Manual
            ? await ManualAdapterWorkflow.RunAsync(chatClient, input)
            : await SequentialAgentWorkflow.RunAsync(chatClient, input);

        Console.WriteLine();
        Console.WriteLine($"output: {Quote(result)}");
        return 0;
    }

    // ─────────────── arg / env plumbing ───────────────

    private enum Mode { Sequential, Manual }

    private static (Mode mode, string input) ParseArgs(string[] args)
    {
        var mode = Mode.Sequential;
        var input = DefaultInput;

        foreach (var arg in args)
        {
            switch (arg)
            {
                case "--manual":
                    mode = Mode.Manual;
                    break;
                case "--sequential":
                    mode = Mode.Sequential;
                    break;
                default:
                    input = arg;
                    break;
            }
        }

        return (mode, input);
    }

    /// <summary>
    /// Builds an <see cref="IChatClient"/> from the current environment.
    /// Mirrors the convention used in Ch01 — <c>LLM_PROVIDER=openai|azure</c>.
    /// </summary>
    internal static IChatClient BuildChatClient()
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
            return azureClient.GetChatClient(deployment).AsIChatClient();
        }

        var openAiKey = Required("OPENAI_API_KEY");
        var model = Environment.GetEnvironmentVariable("LLM_MODEL") ?? "gpt-4.1";
        return new OpenAIClient(new ApiKeyCredential(openAiKey))
            .GetChatClient(model)
            .AsIChatClient();
    }

    /// <summary>
    /// Builds a single translation agent whose instructions pin it to one language.
    /// </summary>
    internal static AIAgent TranslationAgent(IChatClient chatClient, string targetLanguage, string id) =>
        chatClient.AsAIAgent(
            instructions:
                $"You are a translation assistant. Translate the user's message to {targetLanguage}. " +
                "Output ONLY the translation — no quotes, no preamble, no explanation.",
            name: id);

    private static string Required(string name) =>
        Environment.GetEnvironmentVariable(name)
            ?? throw new InvalidOperationException($"{name} must be set (see repo-root .env).");

    private static string Quote(string s) => $"'{s}'";

    /// <summary>
    /// Walks up from <see cref="AppContext.BaseDirectory"/> until it finds a
    /// <c>.env</c> and loads key=value lines into the process env (skipping
    /// comments and blanks). Does not overwrite variables already set.
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

// ─────────────── Pattern 1 — Sequential (convenience) ───────────────
//
// AgentWorkflowBuilder.BuildSequential(...) wraps each AIAgent as an
// executor, wires them in order, and handles the TurnToken dance for you.
// The workflow takes a List<ChatMessage> in and emits a List<ChatMessage>
// containing the full conversation.

/// <summary>
/// Uses <see cref="AgentWorkflowBuilder.BuildSequential(AIAgent[])"/> —
/// the one-liner for a pure agent-to-agent pipeline. No custom executors,
/// no adapter code, no TurnToken wiring by hand.
/// </summary>
internal static class SequentialAgentWorkflow
{
    public static async Task<string> RunAsync(IChatClient chatClient, string input)
    {
        AIAgent enToFr = Program.TranslationAgent(chatClient, "French", id: "en-to-fr");
        AIAgent frToEs = Program.TranslationAgent(chatClient, "Spanish", id: "fr-to-es");

        // The whole chain, wrapped and wired in one call. BuildSequential
        // inserts the input/output adapters internally so the workflow
        // takes a List<ChatMessage> in and surfaces a List<ChatMessage> out.
        Workflow workflow = AgentWorkflowBuilder.BuildSequential(enToFr, frToEs);

        var messages = new List<ChatMessage> { new(ChatRole.User, input) };

        await using StreamingRun run = await InProcessExecution.RunStreamingAsync(workflow, messages);

        // TurnToken triggers the wrapped agents: AgentExecutor caches
        // inbound messages and only calls the LLM once a TurnToken arrives.
        await run.TrySendMessageAsync(new TurnToken(emitEvents: true));

        string finalText = string.Empty;
        string? lastTurnExecutor = null;
        var buffer = new System.Text.StringBuilder();

        await foreach (WorkflowEvent evt in run.WatchStreamAsync())
        {
            switch (evt)
            {
                case AgentResponseUpdateEvent update when update.Data is AgentResponseUpdate chunk:
                    // Streaming: one update per chunk. We buffer per executor
                    // and flush when the next executor takes over — gives the
                    // user an agent-by-agent readout.
                    if (lastTurnExecutor is not null && update.ExecutorId != lastTurnExecutor)
                    {
                        Console.WriteLine($"  [{lastTurnExecutor}] {buffer}");
                        buffer.Clear();
                    }
                    buffer.Append(chunk.Text);
                    lastTurnExecutor = update.ExecutorId;
                    break;

                case WorkflowOutputEvent outputEvent when outputEvent.Data is List<ChatMessage> conversation:
                    // Flush the last executor's accumulated output.
                    if (lastTurnExecutor is not null && buffer.Length > 0)
                    {
                        Console.WriteLine($"  [{lastTurnExecutor}] {buffer}");
                        buffer.Clear();
                    }

                    // Terminal: the full conversation the pipeline produced.
                    // Last assistant message is the Spanish translation.
                    var last = conversation.LastOrDefault(m => m.Role == ChatRole.Assistant);
                    if (last is not null)
                    {
                        finalText = last.Text;
                    }
                    break;

                case ExecutorFailedEvent failed:
                    throw new InvalidOperationException(
                        $"Executor '{failed.ExecutorId}' failed: {failed.Data}");
            }
        }

        return finalText;
    }
}

// ─────────────── Pattern 2 — Manual adapter executors ───────────────
//
// The convenience builder is great when you have a straight agent chain.
// The moment you want to mix agents with raw data executors, emit domain
// events, or manage sessions yourself, you write custom executors that
// call agent.RunAsync(...) from inside [MessageHandler]. Those custom
// executors are the "adapter executors" from the Python tutorial: they
// marshal plain messages (here: str) into agent calls and back.

/// <summary>
/// Pattern 2: Input adapter + two custom agent-backed executors + output adapter,
/// all wired with the source-generated <c>[MessageHandler]</c> pipeline from Ch09.
/// </summary>
internal static class ManualAdapterWorkflow
{
    public static async Task<string> RunAsync(IChatClient chatClient, string input)
    {
        var inputAdapter = new InputAdapter();
        var frenchExecutor = new TranslationAgentExecutor("en-to-fr", chatClient, "French");
        var spanishExecutor = new TranslationAgentExecutor("fr-to-es", chatClient, "Spanish");
        var outputAdapter = new OutputAdapter();

        Workflow workflow = new WorkflowBuilder(inputAdapter)
            .AddEdge(inputAdapter, frenchExecutor)
            .AddEdge(frenchExecutor, spanishExecutor)
            .AddEdge(spanishExecutor, outputAdapter)
            .WithOutputFrom(outputAdapter)
            .Build();

        string finalText = string.Empty;

        await using StreamingRun run = await InProcessExecution.RunStreamingAsync(workflow, input);
        await foreach (WorkflowEvent evt in run.WatchStreamAsync())
        {
            switch (evt)
            {
                case TranslationCompletedEvent progress:
                    Console.WriteLine($"  [{progress.ExecutorId}] {progress.Text}");
                    break;

                case WorkflowOutputEvent output when output.Data is string s:
                    finalText = s;
                    break;

                case ExecutorFailedEvent failed:
                    throw new InvalidOperationException(
                        $"Executor '{failed.ExecutorId}' failed: {failed.Data}");
            }
        }

        return finalText;
    }
}

/// <summary>
/// Custom event raised when a translation executor finishes one LLM turn.
/// </summary>
internal sealed class TranslationCompletedEvent(string executorId, string text)
    : WorkflowEvent(new TranslationPayload(executorId, text))
{
    public string ExecutorId => ((TranslationPayload)Data!).ExecutorId;
    public string Text => ((TranslationPayload)Data!).Text;
}

internal sealed record TranslationPayload(string ExecutorId, string Text);

/// <summary>
/// Input adapter: turns the raw workflow input (a plain string) into the
/// message shape the downstream translation executor wants. In .NET, agent
/// executors consume strings directly — this adapter exists for symmetry
/// with the output adapter and to show where you'd coerce into a richer
/// DTO (think: a validated request object) in a real pipeline.
/// </summary>
[SendsMessage(typeof(string))]
internal sealed partial class InputAdapter() : Executor("input-adapter")
{
    [MessageHandler]
    public async ValueTask HandleAsync(
        string message,
        IWorkflowContext context,
        CancellationToken cancellationToken = default)
    {
        // Forward the message unchanged; this is where request validation,
        // enrichment, or shape coercion would live in a production workflow.
        await context.SendMessageAsync(message, cancellationToken);
    }
}

/// <summary>
/// Adapter executor that wraps an <see cref="AIAgent"/>. Takes a string in,
/// runs one LLM turn, forwards the translation downstream, and emits a
/// custom event so the caller can render per-turn progress.
/// </summary>
/// <remarks>
/// This is the "adapter" from the Python tutorial rendered in idiomatic
/// .NET: a custom <see cref="Executor"/> with a <c>[MessageHandler]</c>
/// that delegates to <c>agent.RunAsync(...)</c>. You reach for this
/// whenever <c>AgentWorkflowBuilder.BuildSequential</c> is too rigid —
/// for custom sessions, tool approval gates, error handling, or custom
/// event payloads.
/// </remarks>
[SendsMessage(typeof(string))]
internal sealed partial class TranslationAgentExecutor : Executor
{
    private readonly AIAgent _agent;

    public TranslationAgentExecutor(string id, IChatClient chatClient, string targetLanguage)
        : base(id)
    {
        _agent = Program.TranslationAgent(chatClient, targetLanguage, id);
    }

    [MessageHandler]
    public async ValueTask HandleAsync(
        string message,
        IWorkflowContext context,
        CancellationToken cancellationToken = default)
    {
        AgentResponse response = await _agent.RunAsync(message, cancellationToken: cancellationToken);
        var translation = response.Text;

        await context.AddEventAsync(new TranslationCompletedEvent(Id, translation), cancellationToken);
        await context.SendMessageAsync(translation, cancellationToken);
    }
}

/// <summary>
/// Output adapter: takes the last translation off the pipeline and yields
/// it as the workflow's terminal output. In a production workflow this is
/// where you'd shape the response into an API-facing DTO.
/// </summary>
[YieldsOutput(typeof(string))]
internal sealed partial class OutputAdapter() : Executor("output-adapter")
{
    [MessageHandler]
    public async ValueTask HandleAsync(
        string message,
        IWorkflowContext context,
        CancellationToken cancellationToken = default)
    {
        await context.YieldOutputAsync(message, cancellationToken);
    }
}
