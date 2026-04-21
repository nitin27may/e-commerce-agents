// MAF v1 — Chapter 13: Concurrent Orchestration (.NET)
//
// Three agents review the same product idea in parallel. Researcher flags
// market fit, Marketer proposes positioning, Legal raises one regulatory
// concern. AgentWorkflowBuilder.BuildConcurrent fans the input out to all
// three; a custom aggregator fans their outputs back in as one synthesised
// summary message.
//
// Run:
//   cd tutorials/13-concurrent-orchestration/dotnet
//   dotnet run                              # default idea
//   dotnet run -- "ultrasonic pet collar"   # custom idea
//
// Requires OPENAI_API_KEY (or Azure OpenAI env vars) in repo-root .env.

using System.ClientModel;
using System.Diagnostics;
using System.Text;
using Azure.AI.OpenAI;
using Microsoft.Agents.AI;
using Microsoft.Agents.AI.Workflows;
using Microsoft.Extensions.AI;
using OpenAI;
using OpenAI.Chat;

using ChatMessage = Microsoft.Extensions.AI.ChatMessage;

namespace MafV1.Ch13.Concurrent;

public static class Program
{
    private const string ResearcherInstructions =
        "You are a Market Researcher. In ONE sentence, assess the market fit of the product idea the user provides.";

    private const string MarketerInstructions =
        "You are a Marketer. In ONE sentence, propose a positioning angle for the product idea the user provides.";

    private const string LegalInstructions =
        "You are a Legal advisor. In ONE sentence, flag ONE regulatory or IP concern about the product idea.";

    public static async Task<int> Main(string[] args)
    {
        LoadDotEnv();

        string idea = args.Length > 0 ? args[0] : "a subscription box for rare herbal teas";
        Console.WriteLine($"Idea: {idea}");
        Console.WriteLine();

        ChatClient chatClient = BuildChatClient();

        AIAgent researcher = chatClient.AsAIAgent(instructions: ResearcherInstructions, name: "researcher");
        AIAgent marketer = chatClient.AsAIAgent(instructions: MarketerInstructions, name: "marketer");
        AIAgent legal = chatClient.AsAIAgent(instructions: LegalInstructions, name: "legal");

        // BuildConcurrent takes an optional aggregator:
        //   Func<IList<List<ChatMessage>>, List<ChatMessage>>
        // Each outer-list entry is one agent's emitted messages, in the
        // same order as the agents were passed in. Our aggregator reduces
        // three message lists into one synthesised summary message.
        Workflow workflow = AgentWorkflowBuilder.BuildConcurrent(
            new[] { researcher, marketer, legal },
            aggregator: SynthesizeReview);

        var stopwatch = Stopwatch.StartNew();
        var messages = new List<ChatMessage> { new(ChatRole.User, idea) };

        await using StreamingRun run = await InProcessExecution.RunStreamingAsync(workflow, messages);
        await run.TrySendMessageAsync(new TurnToken(emitEvents: true));

        List<ChatMessage>? aggregated = null;
        await foreach (WorkflowEvent evt in run.WatchStreamAsync())
        {
            switch (evt)
            {
                case AgentResponseEvent response:
                    // One event per agent, fired once that agent finishes.
                    // Prefer this when you only care about the final
                    // verdict; use AgentResponseUpdateEvent for token-by-
                    // token streaming.
                    Console.WriteLine($"[{response.ExecutorId}] {response.Response.Text.Trim()}");
                    Console.WriteLine();
                    break;

                case WorkflowOutputEvent output when output.Data is List<ChatMessage> list:
                    // The aggregator's return value surfaces as the
                    // workflow's terminal output — see SynthesizeReview.
                    aggregated = list;
                    break;
            }
        }

        stopwatch.Stop();

        Console.WriteLine("===== Aggregated summary =====");
        if (aggregated is not null)
        {
            foreach (ChatMessage message in aggregated)
            {
                Console.WriteLine(message.Text.Trim());
            }
        }
        Console.WriteLine();
        Console.WriteLine($"Wall-clock: {stopwatch.Elapsed.TotalSeconds:F2}s (three LLM calls ran in parallel)");

        return 0;
    }

    /// <summary>
    /// Fan-in aggregator. Receives one <see cref="ChatMessage"/> list per
    /// concurrent agent — same order as the agents were passed in — and
    /// returns a single list representing the workflow's terminal output.
    /// </summary>
    /// <remarks>
    /// This runs after every concurrent branch has completed. No LLM call;
    /// the function is deterministic so the wall-clock stays dominated by
    /// the slowest agent. If you want a synthesising LLM summary instead,
    /// call an agent inside this function — the signature is an async
    /// boundary, so it's safe to await.
    /// </remarks>
    private static List<ChatMessage> SynthesizeReview(IList<List<ChatMessage>> perAgentMessages)
    {
        var builder = new StringBuilder();
        builder.AppendLine("Cross-functional review:");

        foreach (List<ChatMessage> agentOutput in perAgentMessages)
        {
            if (agentOutput.Count == 0)
            {
                continue;
            }

            // The last assistant message per agent is that agent's verdict;
            // earlier messages (if any) are tool calls or scratch turns.
            ChatMessage final = agentOutput[^1];
            string label = final.AuthorName ?? "agent";
            builder.Append("- ").Append(label).Append(": ").AppendLine(final.Text.Trim());
        }

        return new List<ChatMessage>
        {
            new(ChatRole.Assistant, builder.ToString().TrimEnd())
            {
                AuthorName = "concurrent-aggregator",
            },
        };
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
