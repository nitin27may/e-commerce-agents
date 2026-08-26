// MAF v1 — Chapter 15: Group Chat Orchestration (.NET)
//
// Three agents — Writer, Critic, Editor — collaborate on a short piece of
// copy. A centralized manager picks who speaks next each round. This sample
// demonstrates two manager strategies:
//
//   1. RoundRobinGroupChatManager (built-in) — Writer, Critic, Editor in order.
//   2. PromptDrivenManager (custom subclass of GroupChatManager) — a small
//      LLM call picks the next speaker from the participant list based on the
//      conversation so far. Included so you can see that "prompt-driven"
//      doesn't need a separate product type; it's just a manager that calls
//      the LLM inside SelectNextAgentAsync.
//
// Run:
//   cd tutorials/15-group-chat-orchestration/dotnet
//   dotnet run                                         # round-robin
//   dotnet run -- "slogan for a bookstore"             # round-robin, custom topic
//   dotnet run -- "slogan for a bookstore" prompt      # prompt-driven manager

using System.ClientModel;
using System.Text;
using System.Text.Json;
using Azure.AI.OpenAI;
using Microsoft.Agents.AI;
using Microsoft.Agents.AI.Workflows;
using Microsoft.Extensions.AI;
using OpenAI;
using OpenAI.Chat;

using ChatMessage = Microsoft.Extensions.AI.ChatMessage;

namespace MafV1.Ch15.GroupChat;

public static class Program
{
    internal const string WriterInstructions =
        "You are a Writer. Draft or revise copy the user asks for. "
        + "Output exactly one short line — no preamble.";

    internal const string CriticInstructions =
        "You are a Critic. Read the Writer's latest draft and respond in one "
        + "sentence pointing out one concrete improvement. Do not rewrite.";

    internal const string EditorInstructions =
        "You are an Editor. Given the Writer's draft and the Critic's feedback, "
        + "produce the final polished line. Output exactly one short line — no preamble.";

    public static async Task<int> Main(string[] args)
    {
        LoadDotEnv();

        string topic = args.Length > 0 ? args[0] : "slogan for a coffee shop";
        string strategy = args.Length > 1 ? args[1].ToLowerInvariant() : "round-robin";

        Console.WriteLine($"Topic: {topic}");
        Console.WriteLine($"Manager: {strategy}");
        Console.WriteLine();

        IReadOnlyList<ChatMessage> conversation =
            await RunAsync(BuildChatClient().AsIChatClient(), topic, strategy);

        Console.WriteLine("===== Final conversation =====");
        foreach (ChatMessage message in conversation)
        {
            string author = message.AuthorName ?? message.Role.Value;
            Console.WriteLine($"{author}: {message.Text.Trim()}");
        }

        return 0;
    }

    /// <summary>
    /// Builds the Writer/Critic/Editor group chat under one of two managers.
    /// </summary>
    /// <param name="chatClient">
    /// Used both to back the three agents and, under the "prompt" strategy, to
    /// drive <see cref="PromptDrivenManager"/>'s speaker selection. Taking it
    /// as a parameter is what makes the chapter testable.
    /// </param>
    /// <param name="strategy">"prompt" for LLM-selected speakers, anything else for round-robin.</param>
    /// <param name="maximumIterations">
    /// Speaker-turn cap. This is the safety net that stops a bad selector
    /// looping forever, so it is deliberately settable and deliberately tested.
    /// </param>
    public static Workflow BuildWorkflow(
        IChatClient chatClient,
        string strategy = "round-robin",
        int maximumIterations = 3)
    {
        AIAgent writer = chatClient.AsAIAgent(instructions: WriterInstructions, name: "writer");
        AIAgent critic = chatClient.AsAIAgent(instructions: CriticInstructions, name: "critic");
        AIAgent editor = chatClient.AsAIAgent(instructions: EditorInstructions, name: "editor");

        // CreateGroupChatBuilderWith takes a factory:
        //   (IReadOnlyList<AIAgent>) => GroupChatManager
        // The framework hands the participant list to the factory so the
        // manager can see exactly who it is coordinating.
        return strategy == "prompt"
            ? AgentWorkflowBuilder
                .CreateGroupChatBuilderWith(agents => new PromptDrivenManager(agents, chatClient)
                {
                    MaximumIterationCount = maximumIterations,
                })
                .AddParticipants(writer, critic, editor)
                .Build()
            : AgentWorkflowBuilder
                .CreateGroupChatBuilderWith(agents => new RoundRobinGroupChatManager(agents)
                {
                    MaximumIterationCount = maximumIterations,
                })
                .AddParticipants(writer, critic, editor)
                .Build();
    }

    /// <summary>Runs the group chat and returns the full conversation.</summary>
    public static async Task<IReadOnlyList<ChatMessage>> RunAsync(
        IChatClient chatClient,
        string topic,
        string strategy = "round-robin",
        int maximumIterations = 3)
    {
        Workflow workflow = BuildWorkflow(chatClient, strategy, maximumIterations);

        // Group chat workflows wait for a TurnToken before they dispatch the
        // first speaker — same pattern as Ch13's concurrent builder.
        var messages = new List<ChatMessage> { new(ChatRole.User, topic) };

        await using StreamingRun run = await InProcessExecution.RunStreamingAsync(workflow, messages);
        await run.TrySendMessageAsync(new TurnToken(emitEvents: true));

        List<ChatMessage>? finalConversation = null;
        string? currentSpeaker = null;
        var turnBuffer = new StringBuilder();

        await foreach (WorkflowEvent evt in run.WatchStreamAsync())
        {
            switch (evt)
            {
                case AgentResponseUpdateEvent update:
                    // Group chat emits AgentResponseUpdateEvent per streamed
                    // token chunk. Buffer by ExecutorId and flush on change
                    // to print one line per speaker turn.
                    if (update.ExecutorId != currentSpeaker)
                    {
                        FlushTurn(currentSpeaker, turnBuffer);
                        currentSpeaker = update.ExecutorId;
                    }
                    turnBuffer.Append(update.Update.Text);
                    break;

                case WorkflowOutputEvent output when output.Data is List<ChatMessage> conversation:
                    finalConversation = conversation;
                    break;
            }
        }

        FlushTurn(currentSpeaker, turnBuffer);

        return finalConversation ?? new List<ChatMessage>();
    }

    private static void FlushTurn(string? speaker, StringBuilder buffer)
    {
        if (speaker is null || buffer.Length == 0)
        {
            return;
        }
        // ExecutorId is "<agent-name>_<guid>" — strip the suffix for display.
        int underscore = speaker.IndexOf('_');
        string label = underscore > 0 ? speaker[..underscore] : speaker;
        Console.WriteLine($"[{label}] {buffer.ToString().Trim()}");
        Console.WriteLine();
        buffer.Clear();
    }

    /// <summary>
    /// Custom prompt-driven manager. Each round, asks the LLM to pick which
    /// participant should speak next, given the conversation so far and the
    /// list of available agents. Demonstrates that "prompt-driven" is not a
    /// separate MAF product type — it's a GroupChatManager subclass whose
    /// SelectNextAgentAsync calls the LLM instead of walking an index.
    /// </summary>
    /// <remarks>
    /// Production notes:
    /// - Keep the selector prompt cheap: short context, deterministic output.
    /// - Always fall back to a safe default if the LLM returns an unknown name.
    /// - Terminate explicitly when the Editor has spoken.
    /// - ALWAYS chain to base.ShouldTerminateAsync. MaximumIterationCount is
    ///   enforced there, so an override that does not call it silently removes
    ///   the only bound on the conversation.
    /// </remarks>
    internal sealed class PromptDrivenManager : GroupChatManager
    {
        private readonly IReadOnlyList<AIAgent> _agents;
        private readonly IChatClient _selectorClient;

        public PromptDrivenManager(IReadOnlyList<AIAgent> agents, IChatClient selectorClient)
        {
            _agents = agents;
            _selectorClient = selectorClient;
        }

        protected override async ValueTask<AIAgent> SelectNextAgentAsync(
            IReadOnlyList<ChatMessage> history,
            CancellationToken cancellationToken = default)
        {
            // Ask the LLM to pick the next speaker from the roster. Output is
            // constrained to a tiny JSON blob so parsing stays deterministic.
            string roster = string.Join(", ", _agents.Select(a => a.Name));
            var transcript = new StringBuilder();
            foreach (ChatMessage message in history)
            {
                string author = message.AuthorName ?? message.Role.Value;
                transcript.Append('[').Append(author).Append("] ").AppendLine(message.Text);
            }

            string prompt =
                $"You coordinate a Writer/Critic/Editor group chat.\n"
                + $"Available speakers: {roster}.\n"
                + "Pick the single best next speaker.\n"
                + "Reply with ONLY a JSON object: {\"next\": \"<name>\"}.\n\n"
                + "Conversation so far:\n"
                + transcript.ToString();

            try
            {
                ChatResponse response = await _selectorClient
                    .GetResponseAsync(
                        new List<ChatMessage> { new(ChatRole.System, prompt) },
                        new ChatOptions { Temperature = 0 },
                        cancellationToken)
                    .ConfigureAwait(false);

                string raw = response.Text.Trim();
                string? name = ExtractName(raw);

                AIAgent? match = _agents.FirstOrDefault(a =>
                    string.Equals(a.Name, name, StringComparison.OrdinalIgnoreCase));

                return match ?? _agents[(int)(IterationCount % _agents.Count)];
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"[manager] selection LLM failed: {ex.Message}");
                // Safe fallback: round-robin by iteration.
                return _agents[(int)(IterationCount % _agents.Count)];
            }
        }

        protected override async ValueTask<bool> ShouldTerminateAsync(
            IReadOnlyList<ChatMessage> history,
            CancellationToken cancellationToken = default)
        {
            // The base implementation is where MaximumIterationCount is
            // enforced. Overriding without chaining to it throws the cap away
            // — and the failure is not an error, it is an infinite loop:
            // a selector that never picks the Editor keeps the chat running
            // forever, burning a real provider call every turn.
            //
            // This is the single easiest mistake to make in a custom manager,
            // because the override compiles, reads correctly, and works fine
            // in every run where the model happens to pick the Editor.
            if (await base.ShouldTerminateAsync(history, cancellationToken).ConfigureAwait(false))
            {
                return true;
            }

            // Our own condition: stop once the Editor has produced a final line.
            return history.Any(m =>
                string.Equals(m.AuthorName, "editor", StringComparison.OrdinalIgnoreCase));
        }

        private static string? ExtractName(string raw)
        {
            try
            {
                using JsonDocument doc = JsonDocument.Parse(raw);
                return doc.RootElement.GetProperty("next").GetString();
            }
            catch
            {
                return null;
            }
        }
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
